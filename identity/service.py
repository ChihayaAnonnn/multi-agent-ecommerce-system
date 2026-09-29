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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from db.base import new_id, utcnow
from identity.csrf import CsrfSigner
from identity.models import DEMO_SOURCE, AuthSession, IdentityUser, RefreshToken
from identity.passwords import hash_password, validate_password, verify_password
from identity.rate_limit import RedisRateLimiter
from identity.tokens import TokenCodec
from identity.types import (
    DEMO_ENVIRONMENTS,
    AuthIssue,
    DemoModeDisabled,
    EmailAlreadyRegistered,
    IdentityConfigurationError,
    IdentityDependencyError,
    InvalidCredentials,
    Principal,
    UserView,
    make_principal,
)

# 刷新令牌至少 256 位随机；只保存 SHA-256 十六进制摘要。
REFRESH_TOKEN_BYTES = 32
DEFAULT_SESSION_TTL_SECONDS = 2592000  # 30 天绝对到期

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
    def demo_registration_enabled(self) -> bool:
        return self.demo_mode and self.environment in DEMO_ENVIRONMENTS

    async def register_demo(self, email: str, password: str, client_ip: str) -> UserView:
        """创建演示账号。不发送邮件，也不自动登录。"""
        if not self.demo_registration_enabled:
            raise DemoModeDisabled(
                "当前部署环境不开放演示注册：请使用受控账号或联系运维"
            )
        await self._limiter.check_register(client_ip)

        normalized = normalize_email(email)
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
        """校验密码并创建登录会话；失败原因对外不可区分。"""
        normalized = normalize_email(email)
        await self._limiter.check_login(normalized, client_ip)

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
            await self._limiter.record_login_failure(normalized, client_ip)
            raise InvalidCredentials("邮箱或密码不正确")

        matched = await asyncio.to_thread(verify_password, password, user.password_hash)
        if (
            not matched
            or user.status != "active"
            or (user.source == DEMO_SOURCE and not self.demo_registration_enabled)
        ):
            await self._limiter.record_login_failure(normalized, client_ip)
            raise InvalidCredentials("邮箱或密码不正确")

        return await self._issue_session(user)

    async def _issue_session(self, user: IdentityUser) -> AuthIssue:
        """创建登录会话与第一枚刷新令牌，提交后才签发访问令牌。"""
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
                db.add_all([session, token_row])
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

    # ---------- 供后续任务使用的主体构造 ----------

    def principal_for(self, user_id: str) -> Principal:
        return make_principal(self._tokens.issuer, user_id)
