"""身份服务：演示注册、密码登录、令牌刷新、退出与逐请求验证。

每个写命令自己管理事务边界：先提交 MySQL，再向调用方返回令牌与 Cookie 值。
密码校验的 CPU 工作放在线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import unicodedata
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from identity.csrf import CsrfSigner
from identity.models import DEMO_SOURCE, AuthSession, IdentityUser, RefreshToken
from identity.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from identity.rate_limit import RedisRateLimiter
from identity.tokens import TokenCodec
from identity.types import (
    DEMO_ENVIRONMENTS,
    AuthIssue,
    DemoModeDisabled,
    EmailAlreadyRegistered,
    IdentityConfigurationError,
    IdentityDependencyError,
    InvalidAccessToken,
    InvalidCredentials,
    InvalidCsrf,
    InvalidEmail,
    UnknownAccount,
    UserView,
)
from shared.ids import new_id
from shared.principal import Principal, make_principal
from shared.time import utcnow

# 刷新令牌至少 256 位随机；只保存 SHA-256 十六进制摘要。
REFRESH_TOKEN_BYTES = 32
DEFAULT_SESSION_TTL_SECONDS = 2592000  # 30 天绝对到期

# 与 identity_users.email_normalized 的列宽一致。大小写折叠可能让规范化结果变长
# （例如 ß -> ss），因此必须在写入前校验规范化后的长度，而不是只校验原始输入。
MAX_EMAIL_LENGTH = 320

# 账号不存在时也执行一次真实的 Argon2 校验，抹平与「密码错误」的时序差异。
_TIMING_DUMMY_PASSWORD = "timing-equalization-dummy-password"
_TIMING_DUMMY_HASH: str | None = None


def _timing_dummy_hash() -> str:
    global _TIMING_DUMMY_HASH
    if _TIMING_DUMMY_HASH is None:
        _TIMING_DUMMY_HASH = hash_password(_TIMING_DUMMY_PASSWORD)
    return _TIMING_DUMMY_HASH


def normalize_email(raw: str) -> str:
    """演示邮箱规范化：去首尾空白 + Unicode NFC + 大小写折叠。

    不做邮箱商特定的点号或别名折叠——那会改变用户实际提交的地址语义。
    """
    if not isinstance(raw, str):
        return ""
    return unicodedata.normalize("NFC", raw.strip()).casefold()


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


@asynccontextmanager
async def _database_errors(what: str):
    """把数据库故障统一映射为依赖错误；领域错误原样抛出。"""
    try:
        yield
    except IntegrityError:
        raise
    except SQLAlchemyError as exc:
        raise IdentityDependencyError(f"{what}失败：身份数据库不可用") from exc


class IdentityService:
    """身份领域服务；只消费配置与依赖，不直接接触 HTTP。"""

    def __init__(
        self,
        session_factory,
        token_codec: TokenCodec,
        limiter: RedisRateLimiter,
        csrf: CsrfSigner,
        environment: str,
        demo_mode: bool,
        *,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ):
        if demo_mode and environment not in DEMO_ENVIRONMENTS:
            # 开关与部署环境冲突时拒绝装配，而不是带着错误配置继续运行。
            raise IdentityConfigurationError(
                f"演示模式只允许在 {', '.join(DEMO_ENVIRONMENTS)} 环境启用，"
                f"当前环境为 {environment or '未配置'}"
            )
        if session_ttl_seconds <= 0:
            raise IdentityConfigurationError("登录会话有效期必须为正数")

        self._sessions = session_factory
        self._tokens = token_codec
        self._limiter = limiter
        self._csrf = csrf
        self.environment = environment
        self.demo_mode = demo_mode
        self._session_ttl = timedelta(seconds=session_ttl_seconds)

    # ---------- 演示注册 ----------

    @property
    def demo_environment(self) -> bool:
        """当前部署环境是否允许存在演示账号。"""
        return self.environment in DEMO_ENVIRONMENTS

    @property
    def demo_registration_enabled(self) -> bool:
        return self.demo_mode and self.demo_environment

    async def register_demo(self, email: str, password: str, client_ip: str) -> UserView:
        """创建演示账号。不发送邮件，也不自动登录。"""
        if not self.demo_registration_enabled:
            raise DemoModeDisabled(
                "当前部署环境不开放演示注册：请使用受控账号或联系运维"
            )
        await self._limiter.check_register(client_ip)

        normalized = normalize_email(email)
        if not normalized or len(normalized) > MAX_EMAIL_LENGTH:
            raise InvalidEmail("邮箱格式不正确")
        validate_password(password)
        password_hash = await asyncio.to_thread(hash_password, password)

        user = IdentityUser(
            id=new_id(),
            email_normalized=normalized,
            password_hash=password_hash,
            source=DEMO_SOURCE,
        )
        async with _database_errors("注册"):
            async with self._sessions() as db:
                db.add(user)
                try:
                    await db.commit()
                except IntegrityError as exc:
                    await db.rollback()
                    raise EmailAlreadyRegistered("该邮箱已注册") from exc

        return UserView(
            id=user.id, email=user.email_normalized, email_verified=False, source=user.source
        )

    # ---------- 密码登录 ----------

    async def login(self, email: str, password: str, client_ip: str) -> AuthIssue:
        """校验密码并创建登录会话；失败原因对外不可区分。

        限流在入口处原子占用一次尝试配额，成功后才释放：失败的尝试一直计入，
        并发请求不会因为「先检查后计数」同时获准。
        """
        normalized = normalize_email(email)
        await self._limiter.check_login(normalized, client_ip)
        if not normalized or len(normalized) > MAX_EMAIL_LENGTH:
            # 长度不合法的登录名不可能对应任何账号，仍按统一凭证错误处理
            raise InvalidCredentials("邮箱或密码不正确")

        async with _database_errors("登录"):
            async with self._sessions() as db:
                user = (
                    await db.execute(
                        select(IdentityUser).where(
                            IdentityUser.email_normalized == normalized
                        )
                    )
                ).scalar_one_or_none()

        if user is None:
            # 账号不存在也做一次等价开销的哈希校验，减少时序枚举
            await asyncio.to_thread(verify_password, password, _timing_dummy_hash())
            raise InvalidCredentials("邮箱或密码不正确")

        matched = await asyncio.to_thread(verify_password, password, user.password_hash)
        if (
            not matched
            or user.status != "active"
            or (user.source == DEMO_SOURCE and not self.demo_registration_enabled)
        ):
            raise InvalidCredentials("邮箱或密码不正确")

        # Redis 释放占用也可能失败。先完成它再落库会话，避免接口返回 503
        # 时留下客户端从未收到令牌的活动会话。
        await self._limiter.clear_login_attempts(normalized, client_ip)
        try:
            return await self._issue_session(user, user.password_hash)
        except InvalidCredentials:
            # 改密或禁用可能恰好发生在密码校验后；最终复核失败仍算一次失败。
            await self._limiter.record_login_failure(normalized, client_ip)
            raise

    async def _issue_session(self, user: IdentityUser, verified_hash: str) -> AuthIssue:
        """创建登录会话与第一枚刷新令牌，提交后才签发访问令牌。

        事务内先锁账号行再复核密码哈希：受控重置（以及禁用、改密）会更新同一行，
        如果不复核，重置就可能提交在「密码校验通过」和「会话落库」之间，
        导致这次用旧密码建立的会话躲过撤销。
        """
        now = utcnow()
        session = AuthSession(
            id=new_id(),
            user_id=user.id,
            created_at=now,
            last_used_at=now,
            expires_at=now + self._session_ttl,
        )
        raw_refresh = new_refresh_token()
        token_row = RefreshToken(
            id=new_id(),
            auth_session_id=session.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=session.expires_at,
            created_at=now,
        )
        async with _database_errors("创建登录会话"):
            async with self._sessions() as db:
                current = (
                    await db.execute(
                        select(IdentityUser)
                        .where(IdentityUser.id == user.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if (
                    current is None
                    or current.password_hash != verified_hash
                    or current.status != "active"
                    or (current.source == DEMO_SOURCE and not self.demo_registration_enabled)
                ):
                    raise InvalidCredentials("账号状态已变化，请重新登录")

                # 会话行先落库再插令牌行，不依赖 ORM 的内部排序
                db.add(session)
                await db.flush()
                db.add(token_row)
                await db.commit()

        return self._issue_tokens(user.id, session.id, raw_refresh, now)

    def _issue_tokens(
        self, user_id: str, session_id: str, raw_refresh: str, now
    ) -> AuthIssue:
        return AuthIssue(
            access_token=self._tokens.issue(user_id, session_id, now),
            refresh_token=raw_refresh,
            csrf_token=self._csrf.issue(session_id),
            expires_at=now + timedelta(seconds=self._tokens.access_ttl_seconds),
            user_id=user_id,
            session_id=session_id,
        )

    # ---------- 受控密码重置 ----------

    async def reset_demo_password(self, user_id: str, new_password: str) -> None:
        """受控运维入口：重置演示账号密码并撤销该用户全部登录会话。

        只允许在演示环境调用，且只作用于 `source=demo` 的账号；命令本身不打印
        任何秘密，失败只回抛脱敏后的领域错误。
        """
        if not self.demo_environment:
            raise DemoModeDisabled(
                f"受控密码重置只允许在 {', '.join(DEMO_ENVIRONMENTS)} 环境执行，"
                f"当前环境为 {self.environment or '未配置'}"
            )
        validate_password(new_password)
        password_hash = await asyncio.to_thread(hash_password, new_password)
        now = utcnow()

        async with _database_errors("重置密码"):
            async with self._sessions() as db:
                # 先锁账号行：与登录的会话创建串行，避免在「会话刚落库」与
                # 「撤销该用户全部会话」之间出现窗口
                user = (
                    await db.execute(
                        select(IdentityUser)
                        .where(IdentityUser.id == user_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if user is None or user.source != DEMO_SOURCE:
                    raise UnknownAccount("账号不存在或不是演示账号")

                # 重置同时撤销全部登录会话与刷新令牌：旧 JWT 立即失效。
                # 加锁读取，拿到的必须是账号锁之后的最新已提交状态。
                sessions = (
                    await db.execute(
                        select(AuthSession)
                        .where(AuthSession.user_id == user.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalars().all()
                for session in sessions:
                    await self._revoke_session(db, session, now, "password_reset")

                user.password_hash = password_hash
                await db.commit()

    # ---------- 逐请求验证 ----------

    async def verify(self, token: str) -> Principal:
        """验签后再查 MySQL：账号与登录会话都必须有效。

        JWT 未过期不代表仍然可用——退出、禁用或重置密码后立即失效。
        数据库故障抛 `IdentityDependencyError`，调用方返回 503，绝不降级为匿名。

        这里是只读路径：`last_used_at` 只在刷新与退出时更新，避免每次读请求都写库。
        """
        now = utcnow()
        claims = self._tokens.decode(token, now)

        async with _database_errors("验证登录状态"):
            async with self._sessions() as db:
                user = await db.get(IdentityUser, claims.user_id)
                session = await db.get(AuthSession, claims.session_id)

        if user is None or session is None or session.user_id != user.id:
            raise InvalidAccessToken("访问令牌对应的账号或登录会话不存在")
        self._ensure_user_usable(user)
        if session.revoked_at is not None:
            raise InvalidAccessToken("登录会话已撤销")
        if session.expires_at <= now:
            raise InvalidAccessToken("登录会话已过期")

        return self.principal_for(user.id)

    async def get_me(self, principal: Principal) -> UserView:
        """返回当前主体的账号视图；演示账号一律 `email_verified=False`。"""
        async with _database_errors("读取账号"):
            async with self._sessions() as db:
                user = await db.get(IdentityUser, principal.subject)
        if user is None:
            raise InvalidAccessToken("主体对应的账号不存在")
        return UserView(
            id=user.id,
            email=user.email_normalized,
            email_verified=False,
            source=user.source,
        )

    # ---------- 刷新轮换与撤销 ----------

    async def csrf_for_refresh(self, raw_refresh: str) -> str:
        """凭刷新 Cookie 定位有效会话并签发会话绑定的 CSRF 值。

        只读：不消费也不轮换刷新令牌，页面重载时可以重复调用。
        """
        now = utcnow()
        async with _database_errors("读取登录会话"):
            async with self._sessions() as db:
                session, _user = await self._load_active_session(db, raw_refresh, now)
        return self._csrf.issue(session.id)

    async def refresh(self, raw_refresh: str, csrf_value: str, client_ip: str) -> AuthIssue:
        """消费旧刷新令牌并签发新的一对令牌。

        并发刷新只有一个成功者：会话行与令牌行按固定顺序加锁，
        失败的一方看到的是「已消费」，按重放处理并撤销整个会话。
        """
        now = utcnow()
        token_digest = hash_refresh_token(raw_refresh)

        # 先只读定位会话，仅用于把限流记到会话维度
        async with _database_errors("读取登录会话"):
            async with self._sessions() as db:
                probe = (
                    await db.execute(
                        select(RefreshToken).where(RefreshToken.token_hash == token_digest)
                    )
                ).scalar_one_or_none()
        await self._limiter.check_refresh(
            probe.auth_session_id if probe is not None else None, client_ip
        )

        async with _database_errors("刷新令牌"):
            async with self._sessions() as db:
                row = (
                    await db.execute(
                        select(RefreshToken).where(RefreshToken.token_hash == token_digest)
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise InvalidCredentials("刷新令牌无效")

                # 固定锁顺序：先会话行，再令牌行。
                # populate_existing：加锁读取必须覆盖身份映射里的旧值，否则并发下
                # 会拿上面那次无锁读的快照，看不到别人刚写入的消费标记。
                session = (
                    await db.execute(
                        select(AuthSession)
                        .where(AuthSession.id == row.auth_session_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if session is None:
                    raise InvalidCredentials("刷新令牌无效")
                locked = (
                    await db.execute(
                        select(RefreshToken)
                        .where(RefreshToken.id == row.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one()

                if not self._csrf.verify(session.id, csrf_value):
                    raise InvalidCsrf("CSRF 校验失败")

                user = await db.get(IdentityUser, session.user_id)
                if user is None:
                    raise InvalidCredentials("刷新令牌无效")

                if locked.consumed_at is not None:
                    # 已消费令牌被重放：只撤销它自己的会话，不影响其他会话
                    await self._revoke_session(db, session, now, "refresh_replay")
                    await db.commit()
                    raise InvalidCredentials("刷新令牌已失效，请重新登录")

                if (
                    locked.revoked_at is not None
                    or locked.expires_at <= now
                    or session.revoked_at is not None
                    or session.expires_at <= now
                ):
                    raise InvalidCredentials("刷新令牌已失效，请重新登录")

                self._ensure_usable_for_refresh(user)

                raw_refresh_next = new_refresh_token()
                locked.consumed_at = now
                session.last_used_at = now
                # 先落消费标记再插入新行：同会话的活动令牌唯一索引要求旧行让位
                await db.flush()
                db.add(
                    RefreshToken(
                        id=new_id(),
                        auth_session_id=session.id,
                        token_hash=hash_refresh_token(raw_refresh_next),
                        expires_at=session.expires_at,
                        created_at=now,
                    )
                )
                await db.commit()

        return self._issue_tokens(user.id, session.id, raw_refresh_next, now)

    async def logout(self, raw_refresh: str, csrf_value: str) -> None:
        """撤销当前登录会话及其全部刷新令牌；之后的 JWT 立即失效。"""
        now = utcnow()
        async with _database_errors("退出登录"):
            async with self._sessions() as db:
                # 固定锁顺序与 refresh 一致：先会话行、再令牌行。
                # 反过来会在与刷新并发时形成 ABBA 死锁。
                located = (
                    await db.execute(
                        select(RefreshToken).where(
                            RefreshToken.token_hash == hash_refresh_token(raw_refresh)
                        )
                    )
                ).scalar_one_or_none()
                if located is None:
                    raise InvalidCredentials("刷新令牌无效")
                session = (
                    await db.execute(
                        select(AuthSession)
                        .where(AuthSession.id == located.auth_session_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if session is None:
                    raise InvalidCredentials("刷新令牌无效")
                # 下面的撤销语句会在会话锁之后才碰令牌行，与 refresh 的顺序一致
                if not self._csrf.verify(session.id, csrf_value):
                    raise InvalidCsrf("CSRF 校验失败")

                await self._revoke_session(db, session, now, "logout")
                session.last_used_at = now
                await db.commit()

    # ---------- 内部工具 ----------

    def _ensure_user_usable(self, user: IdentityUser) -> None:
        """账号状态与来源必须允许在当前环境使用。"""
        if user.status != "active":
            raise InvalidAccessToken("账号已停用")
        if user.source == DEMO_SOURCE and not self.demo_registration_enabled:
            raise InvalidAccessToken("当前部署环境不接受演示账号")

    def _ensure_usable_for_refresh(self, user: IdentityUser) -> None:
        """刷新与取 CSRF 路径把「账号不可用」统一成凭证错误。

        这两个端点对外只有 401（凭证失效）这一种失败语义；直接抛
        `InvalidAccessToken` 会在路由层变成未映射异常 → 500。
        """
        try:
            self._ensure_user_usable(user)
        except InvalidAccessToken as exc:
            raise InvalidCredentials("刷新令牌已失效，请重新登录") from exc

    async def _load_active_session(self, db, raw_refresh: str, now):
        """按刷新令牌哈希定位仍然可用的会话与账号。"""
        row = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(raw_refresh)
                )
            )
        ).scalar_one_or_none()
        if row is None or row.consumed_at is not None or row.revoked_at is not None:
            raise InvalidCredentials("刷新令牌无效")
        if row.expires_at <= now:
            raise InvalidCredentials("刷新令牌已过期")
        session = await db.get(AuthSession, row.auth_session_id)
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            raise InvalidCredentials("登录会话已失效")
        user = await db.get(IdentityUser, session.user_id)
        if user is None:
            raise InvalidCredentials("登录会话已失效")
        self._ensure_usable_for_refresh(user)
        return session, user

    async def _revoke_session(self, db, session: AuthSession, now, reason: str) -> None:
        """撤销会话与它名下所有未撤销的刷新令牌。"""
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoke_reason = reason
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.auth_session_id == session.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    def principal_for(self, user_id: str) -> Principal:
        return make_principal(self._tokens.issuer, user_id)


__all__ = [
    'DEFAULT_SESSION_TTL_SECONDS',
    'IdentityService',
    'MAX_EMAIL_LENGTH',
    'PasswordPolicyError',
    'Principal',
    'REFRESH_TOKEN_BYTES',
    'hash_refresh_token',
    'make_principal',
    'new_refresh_token',
    'normalize_email',
]
