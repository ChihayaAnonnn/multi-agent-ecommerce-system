"""身份领域 MySQL 集成测试：迁移约束、账号写入与令牌轮换。

这些用例必须连真实 MySQL 执行；`ECOM_TEST_DATABASE_URL` 未配置时跳过，
跳过不构成「数据库行为已验证」。
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import secrets
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db.base import utcnow
from db.session import create_session_factory
from identity.csrf import CsrfSigner
from identity.models import AuthSession, IdentityUser, RefreshToken
from identity.passwords import PasswordPolicyError, hash_password, verify_password
from identity.rate_limit import RedisRateLimiter
from identity.service import (
    DEFAULT_SESSION_TTL_SECONDS,
    IdentityService,
    hash_refresh_token,
    normalize_email,
)
from identity.tokens import TokenCodec
from identity.types import (
    AuthIssue,
    DemoModeDisabled,
    EmailAlreadyRegistered,
    IdentityConfigurationError,
    IdentityDependencyError,
    InvalidAccessToken,
    InvalidCredentials,
    InvalidCsrf,
    UnknownAccount,
    UserView,
)
from tests.conftest import downgrade_to, read_schema, upgrade_to

IDENTITY_TABLES = {"identity_users", "identity_auth_sessions", "identity_refresh_tokens"}

IDENTITY_ISSUER = "https://identity.ecom.test"
IDENTITY_AUDIENCE = "ecom-api"


async def _query(database_url: str, statement, **params) -> list[tuple]:
    async with create_session_factory(database_url)() as session:
        result = await session.execute(text(statement), params)
        return [tuple(row) for row in result]


def query(database_url: str, statement, **params) -> list[tuple]:
    return asyncio.run(_query(database_url, statement, **params))


SESSION_TTL = timedelta(days=30)


async def _create_user(
    factory,
    email: str,
    *,
    password_hash: str = "$argon2id$placeholder",
    status: str = "active",
    source: str = "demo",
) -> IdentityUser:
    """写入一个账号；外键无 ORM relationship，必须与登录会话分开提交。"""
    user = IdentityUser(
        id=str(uuid.uuid4()),
        email_normalized=email,
        password_hash=password_hash,
        status=status,
        source=source,
    )
    async with factory() as db:
        db.add(user)
        await db.commit()
    return user


async def _create_auth_session(
    factory, user_id: str, *, now=None, ttl: timedelta = SESSION_TTL
) -> AuthSession:
    moment = now or utcnow()
    session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        created_at=moment,
        last_used_at=moment,
        expires_at=moment + ttl,
    )
    async with factory() as db:
        db.add(session)
        await db.commit()
    return session


def _refresh_token(
    session_id: str,
    *,
    now=None,
    consumed: bool = False,
    revoked: bool = False,
    ttl: timedelta = SESSION_TTL,
) -> RefreshToken:
    moment = now or utcnow()
    return RefreshToken(
        id=str(uuid.uuid4()),
        auth_session_id=session_id,
        token_hash=secrets.token_hex(32),
        expires_at=moment + ttl,
        created_at=moment,
        consumed_at=moment if consumed else None,
        revoked_at=moment if revoked else None,
    )


# ---------- 迁移 ----------


def test_identity_migration_constraints(migrated_mysql_database: str):
    """0004 迁移建立三张身份表、唯一约束、外键与活动令牌唯一索引，且可升可降。"""
    downgrade_to(migrated_mysql_database, "0003_inventory")
    assert not (IDENTITY_TABLES & read_schema(migrated_mysql_database)["tables"])

    upgrade_to(migrated_mysql_database, "0004_identity")
    schema = read_schema(migrated_mysql_database)

    assert IDENTITY_TABLES <= schema["tables"]
    assert ("identity_users", ("email_normalized",)) in schema["uniques"]
    assert ("identity_refresh_tokens", ("token_hash",)) in schema["uniques"]
    # 每会话最多一枚活动令牌：活动行生成列上的唯一索引
    assert ("identity_refresh_tokens", ("active_auth_session_id",)) in schema["uniques"]

    assert {"id", "email_normalized", "password_hash", "status", "source"} <= schema[
        "columns"
    ]["identity_users"]
    assert {"user_id", "created_at", "last_used_at", "expires_at", "revoked_at"} <= schema[
        "columns"
    ]["identity_auth_sessions"]
    assert {"auth_session_id", "token_hash", "consumed_at", "active_auth_session_id"} <= schema[
        "columns"
    ]["identity_refresh_tokens"]

    # 主键与时间列形状：UUID CHAR(36) 与 UTC DATETIME(6)
    id_type = query(
        migrated_mysql_database,
        "SELECT column_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'identity_users' "
        "AND column_name = 'id'",
    )[0]
    assert id_type[0] == "char(36)"
    assert id_type[1] == "NO"
    expires_type = query(
        migrated_mysql_database,
        "SELECT column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'identity_auth_sessions' "
        "AND column_name = 'expires_at'",
    )[0][0]
    assert expires_type == "datetime(6)"

    # 活动列是 STORED 生成列，否则唯一索引无法表达「活动令牌」
    extra = query(
        migrated_mysql_database,
        "SELECT extra, generation_expression FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'identity_refresh_tokens' "
        "AND column_name = 'active_auth_session_id'",
    )[0]
    assert "STORED GENERATED" in extra[0].upper()

    foreign_keys = set(
        query(
            migrated_mysql_database,
            "SELECT table_name, column_name, referenced_table_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL",
        )
    )
    assert (
        "identity_auth_sessions",
        "user_id",
        "identity_users",
    ) in foreign_keys
    assert (
        "identity_refresh_tokens",
        "auth_session_id",
        "identity_auth_sessions",
    ) in foreign_keys

    # 可降回 0003 并再次升级（迁移必须可逆）
    downgrade_to(migrated_mysql_database, "0003_inventory")
    assert not (IDENTITY_TABLES & read_schema(migrated_mysql_database)["tables"])
    upgrade_to(migrated_mysql_database, "0004_identity")
    assert IDENTITY_TABLES <= read_schema(migrated_mysql_database)["tables"]


def test_only_one_active_refresh_token_per_session(migrated_mysql_database: str):
    """同一登录会话最多一行活动刷新令牌；旧行被消费后槽位释放。"""

    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        now = utcnow()
        user = await _create_user(factory, "driver@example.com")
        session = await _create_auth_session(factory, user.id)

        first = _refresh_token(session.id)
        async with factory() as db:
            db.add(first)
            await db.commit()

        # 第二枚活动令牌被唯一索引拒绝
        async with factory() as db:
            db.add(_refresh_token(session.id))
            with pytest.raises(IntegrityError):
                await db.commit()

        # 消费旧行后可以签发新行
        async with factory() as db:
            stored = await db.get(RefreshToken, first.id)
            stored.consumed_at = now
            await db.commit()
        second = _refresh_token(session.id)
        async with factory() as db:
            db.add(second)
            await db.commit()

        # 已撤销（或已消费）的行不占用活动槽位
        async with factory() as db:
            stored = await db.get(RefreshToken, second.id)
            stored.revoked_at = now
            await db.commit()
        async with factory() as db:
            db.add(_refresh_token(session.id, revoked=True))
            await db.commit()

        async def active_rows() -> int:
            rows = await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_refresh_tokens "
                "WHERE active_auth_session_id = :sid",
                sid=session.id,
            )
            return rows[0][0]

        assert await active_rows() == 0

        # 槽位重新被占用后，第三枚活动令牌依旧被拒绝
        async with factory() as db:
            db.add(_refresh_token(session.id))
            await db.commit()
        assert await active_rows() == 1
        async with factory() as db:
            db.add(_refresh_token(session.id))
            with pytest.raises(IntegrityError):
                await db.commit()

    asyncio.run(scenario())


def test_token_hash_columns_store_only_digests(migrated_mysql_database: str):
    """刷新令牌只存 SHA-256 哈希：列宽固定，写入的密文长度不随原串增长。"""

    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        user = await _create_user(factory, "hash@example.com")
        session = await _create_auth_session(factory, user.id)
        raw_token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        row = _refresh_token(session.id)
        row.token_hash = digest
        async with factory() as db:
            db.add(row)
            await db.commit()

        stored = (
            await _query(
                migrated_mysql_database,
                "SELECT token_hash FROM identity_refresh_tokens "
                "WHERE auth_session_id = :sid",
                sid=session.id,
            )
        )[0][0]
        assert stored == digest
        assert raw_token not in stored
        assert len(stored) == 64

    asyncio.run(scenario())


def test_email_uniqueness_after_normalization(migrated_mysql_database: str):
    """规范化后的邮箱唯一：大小写与 NFC 等价的账号不能重复建档。"""

    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        await _create_user(factory, "demo@example.com")

        async with factory() as db:
            db.add(
                IdentityUser(
                    id=str(uuid.uuid4()),
                    email_normalized="demo@example.com",
                    password_hash="$argon2id$placeholder",
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()

    asyncio.run(scenario())


# ---------- 演示注册与密码登录 ----------


@pytest.fixture(scope="module")
def identity_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_pem, public_pem


@pytest.fixture(scope="module")
def identity_codec(identity_keypair) -> TokenCodec:
    private_pem, public_pem = identity_keypair
    return TokenCodec(
        issuer=IDENTITY_ISSUER,
        audience=IDENTITY_AUDIENCE,
        active_kid="test-key-1",
        private_key_pem=private_pem,
        public_keys={"test-key-1": public_pem},
    )


CSRF_SECRET = "c" * 48


async def _build_service(
    database_url: str,
    redis_url: str,
    codec: TokenCodec,
    *,
    environment: str = "test",
    demo_mode: bool = True,
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    limiter: RedisRateLimiter | None = None,
    csrf: CsrfSigner | None = None,
) -> IdentityService:
    """在事件循环内构造服务；Redis 连接必须属于当前循环。"""
    if limiter is None:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        limiter = RedisRateLimiter(client)
    return IdentityService(
        create_session_factory(database_url),
        codec,
        limiter,
        csrf or CsrfSigner(CSRF_SECRET),
        environment,
        demo_mode,
        session_ttl_seconds=session_ttl_seconds,
    )


async def _load_user(database_url: str, user_id: str) -> IdentityUser:
    async with create_session_factory(database_url)() as db:
        return await db.get(IdentityUser, user_id)


async def _disable_user(database_url: str, user_id: str) -> None:
    factory = create_session_factory(database_url)
    async with factory() as db:
        user = await db.get(IdentityUser, user_id)
        user.status = "disabled"
        await db.commit()


def test_demo_register_and_login(migrated_mysql_database: str, clean_redis: str, identity_codec):
    """注册只写账号不自动登录；登录返回可验签的令牌且主体互不相同。"""
    password = "a-long-enough-demo-password"

    async def scenario():
        csrf = CsrfSigner(CSRF_SECRET)
        service = await _build_service(
            migrated_mysql_database, clean_redis, identity_codec, csrf=csrf
        )
        view = await service.register_demo("Demo@Example.com ", password, "203.0.113.7")

        assert view.email == "demo@example.com"
        assert view.email_verified is False
        assert view.source == "demo"

        stored = await _load_user(migrated_mysql_database, view.id)
        assert stored.password_hash != password
        assert stored.password_hash.startswith("$argon2id$")
        assert password not in stored.password_hash

        # 注册不自动登录：此时还没有任何登录会话
        sessions = (
            await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_auth_sessions WHERE user_id = :uid",
                uid=view.id,
            )
        )[0][0]
        assert sessions == 0

        now = utcnow()
        issue = await service.login("  dEmo@example.COM ", password, "203.0.113.7")
        assert issue.user_id == view.id
        assert issue.refresh_token and len(issue.refresh_token) >= 32
        assert identity_codec.decode(issue.access_token, now).user_id == view.id
        assert identity_codec.decode(issue.access_token, now).session_id == issue.session_id
        assert csrf.verify(issue.session_id, issue.csrf_token) is True
        assert csrf.verify("another-session", issue.csrf_token) is False

        # 另一位用户拿到的是不同主体
        other = await service.register_demo("other@example.com", password, "203.0.113.8")
        other_issue = await service.login("other@example.com", password, "203.0.113.8")
        assert identity_codec.decode(other_issue.access_token, now).user_id == other.id
        assert other.id != view.id

        # 刷新令牌只存哈希
        hashes = await _query(
            migrated_mysql_database,
            "SELECT token_hash FROM identity_refresh_tokens WHERE auth_session_id = :sid",
            sid=issue.session_id,
        )
        assert len(hashes) == 1
        assert hashes[0][0] == hash_refresh_token(issue.refresh_token)
        assert issue.refresh_token not in hashes[0][0]

        # 会话绝对到期为 30 天
        expires = (
            await _query(
                migrated_mysql_database,
                "SELECT created_at, expires_at FROM identity_auth_sessions WHERE id = :sid",
                sid=issue.session_id,
            )
        )[0]
        assert expires[1] - expires[0] == timedelta(days=30)

    asyncio.run(scenario())


def test_concurrent_equivalent_email_registration_creates_one_user(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """大小写与 NFC 等价的邮箱并发注册只产生一名用户。"""
    composed = "café@example.com"
    decomposed = "café@example.com"
    assert composed != decomposed
    assert normalize_email(composed) == normalize_email(decomposed.upper())

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        results = await asyncio.gather(
            service.register_demo(composed, "a-long-enough-password", "203.0.113.21"),
            service.register_demo(
                decomposed.upper(), "another-long-enough-password", "203.0.113.22"
            ),
            return_exceptions=True,
        )

        succeeded = [item for item in results if isinstance(item, UserView)]
        conflicts = [item for item in results if isinstance(item, EmailAlreadyRegistered)]
        assert len(succeeded) == 1, results
        assert len(conflicts) == 1, results
        assert succeeded[0].email == normalize_email(composed)

        rows = (
            await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_users WHERE email_normalized = :email",
                email=normalize_email(composed),
            )
        )[0][0]
        assert rows == 1

    asyncio.run(scenario())


def test_bad_credentials_are_indistinguishable(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """不存在账号、密码错误与禁用账号对外是同一个领域错误。"""
    password = "a-long-enough-demo-password"

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        registered_view = await service.register_demo(
            "real@example.com", password, "203.0.113.31"
        )
        registered = await _load_user(migrated_mysql_database, registered_view.id)
        disabled = await service.register_demo("blocked@example.com", password, "203.0.113.32")
        await _disable_user(migrated_mysql_database, disabled.id)

        errors = []
        for email, candidate in (
            ("ghost@example.com", password),
            ("real@example.com", password + "-wrong"),
            ("blocked@example.com", password),
        ):
            with pytest.raises(InvalidCredentials) as error:
                await service.login(email, candidate, "203.0.113.31")
            errors.append(error.value)

        # 同一个错误类型、同一句对外文案、都没有多余信息
        assert {type(item) for item in errors} == {InvalidCredentials}
        assert len({str(item) for item in errors}) == 1
        assert password not in str(errors[0])
        assert registered.password_hash not in str(errors[0])

    asyncio.run(scenario())


def test_demo_mode_gate(migrated_mysql_database: str, clean_redis: str, identity_codec):
    """正式环境即便误设开关也拒绝装配，且不接受演示账号注册与登录。"""
    password = "a-long-enough-demo-password"

    async def scenario():
        with pytest.raises(IdentityConfigurationError):
            await _build_service(
                migrated_mysql_database,
                clean_redis,
                identity_codec,
                environment="production",
                demo_mode=True,
            )

        service = await _build_service(
            migrated_mysql_database,
            clean_redis,
            identity_codec,
            environment="production",
            demo_mode=False,
        )
        assert service.demo_registration_enabled is False

        with pytest.raises(DemoModeDisabled):
            await service.register_demo("prod@example.com", password, "203.0.113.41")

        # 直接写库造一个演示账号：正式环境的登录同样拒绝它
        factory = create_session_factory(migrated_mysql_database)
        user = await _create_user(
            factory, "prod-demo@example.com", password_hash=hash_password(password)
        )
        with pytest.raises(InvalidCredentials):
            await service.login("prod-demo@example.com", password, "203.0.113.42")
        assert (await _load_user(migrated_mysql_database, user.id)).status == "active"

        # 其他环境标签（例如拼错的 deploy）同样不开放演示模式
        with pytest.raises(IdentityConfigurationError):
            await _build_service(
                migrated_mysql_database,
                clean_redis,
                identity_codec,
                environment="deploy",
                demo_mode=True,
            )

    asyncio.run(scenario())


# ---------- 逐请求验证、刷新轮换与撤销 ----------

DEMO_PASSWORD = "a-long-enough-demo-password"


async def _register_and_login(service, email: str, ip: str, password: str = DEMO_PASSWORD):
    view = await service.register_demo(email, password, ip)
    return view, await service.login(email, password, ip)


def test_verify_rechecks_user_and_session(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """JWT 有效不等于可用：账号、会话与主体配对每次请求都要重查。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        view, issue = await _register_and_login(service, "verify@example.com", "203.0.113.61")
        assert (await service.verify(issue.access_token)).subject == view.id
        me = await service.get_me(await service.verify(issue.access_token))
        assert me.email == "verify@example.com"
        assert me.email_verified is False
        assert me.source == "demo"

        # 会话被撤销
        await service.logout(issue.refresh_token, issue.csrf_token)
        with pytest.raises(InvalidAccessToken):
            await service.verify(issue.access_token)

        # 会话已过期
        _, expired_issue = await _register_and_login(
            service, "expired@example.com", "203.0.113.62"
        )
        await _set_session_expiry(migrated_mysql_database, expired_issue.session_id, utcnow())
        with pytest.raises(InvalidAccessToken):
            await service.verify(expired_issue.access_token)

        # 账号被禁用
        _, disabled_issue = await _register_and_login(
            service, "disabled@example.com", "203.0.113.63"
        )
        await _disable_user(migrated_mysql_database, disabled_issue.user_id)
        with pytest.raises(InvalidAccessToken):
            await service.verify(disabled_issue.access_token)

        # sub 与 sid 不属于同一主体
        _, other_issue = await _register_and_login(
            service, "other-verify@example.com", "203.0.113.64"
        )
        mismatched = identity_codec.issue(view.id, other_issue.session_id, utcnow())
        with pytest.raises(InvalidAccessToken):
            await service.verify(mismatched)

        # 账号被删除
        await _delete_user(migrated_mysql_database, other_issue.user_id)
        with pytest.raises(InvalidAccessToken):
            await service.verify(other_issue.access_token)

        # 签名无效
        with pytest.raises(InvalidAccessToken):
            await service.verify(expired_issue.access_token + "x")

        # 数据库不可用：依赖故障，不是匿名
        offline = await _build_service(
            "mysql+asyncmy://ecom:secret@127.0.0.1:1/ecommerce",
            clean_redis,
            identity_codec,
        )
        with pytest.raises(IdentityDependencyError):
            await offline.verify(issue.access_token)
        with pytest.raises(IdentityDependencyError):
            await offline.login("verify@example.com", DEMO_PASSWORD, "203.0.113.65")

    asyncio.run(scenario())


def test_refresh_rotation_and_replay(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """旧令牌被消费、新哈希落库；重放只撤销它自己的会话，未知令牌不牵连别人。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        view, issue = await _register_and_login(service, "rotate@example.com", "203.0.113.71")
        bystander_view, bystander = await _register_and_login(
            service, "bystander@example.com", "203.0.113.72"
        )

        rotated = await service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.71")
        assert rotated.session_id == issue.session_id
        assert rotated.user_id == view.id
        assert rotated.refresh_token != issue.refresh_token
        assert (await service.verify(rotated.access_token)).subject == view.id

        rows = await _token_rows(migrated_mysql_database, issue.session_id)
        assert len(rows) == 2
        consumed = {token_hash: consumed_at for token_hash, consumed_at in rows}
        assert consumed[hash_refresh_token(issue.refresh_token)] is not None
        assert consumed[hash_refresh_token(rotated.refresh_token)] is None

        # 未知随机令牌：既不通过，也不撤销任何别人的会话
        with pytest.raises(InvalidCredentials):
            await service.refresh(secrets.token_urlsafe(32), "forged", "203.0.113.71")
        assert (await service.verify(bystander.access_token)).subject == bystander_view.id
        assert (
            await _session_state(migrated_mysql_database, bystander.session_id)
        )["revoked_at"] is None

        # 已消费令牌重放：撤销它自己的会话
        with pytest.raises(InvalidCredentials):
            await service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.71")
        revoked = await _session_state(migrated_mysql_database, issue.session_id)
        assert revoked["revoked_at"] is not None
        assert revoked["revoke_reason"] == "refresh_replay"

        # 会话撤销后新签发的 access token 与刷新令牌同时失效
        with pytest.raises(InvalidAccessToken):
            await service.verify(rotated.access_token)
        with pytest.raises(InvalidCredentials):
            await service.refresh(rotated.refresh_token, rotated.csrf_token, "203.0.113.71")

        # 旁观者会话不受影响
        still_valid = await service.refresh(
            bystander.refresh_token, bystander.csrf_token, "203.0.113.72"
        )
        assert still_valid.session_id == bystander.session_id

    asyncio.run(scenario())


def test_concurrent_refresh_has_one_winner(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """两个事务竞争同一令牌只轮换一次，重放方触发会话撤销。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        _, issue = await _register_and_login(service, "race@example.com", "203.0.113.81")

        results = await asyncio.gather(
            service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.81"),
            service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.81"),
            return_exceptions=True,
        )
        winners = [item for item in results if isinstance(item, AuthIssue)]
        losers = [item for item in results if isinstance(item, InvalidCredentials)]
        assert len(winners) == 1, results
        assert len(losers) == 1, results

        # 竞争失败方按重放处理：会话最终必须已撤销
        state = await _session_state(migrated_mysql_database, issue.session_id)
        assert state["revoked_at"] is not None
        assert state["revoke_reason"] == "refresh_replay"

        # 胜者拿到的令牌也随之失效，且会话名下没有残留的活动令牌
        with pytest.raises(InvalidAccessToken):
            await service.verify(winners[0].access_token)
        rows = await _token_rows(migrated_mysql_database, issue.session_id)
        assert len(rows) == 2
        active = (
            await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_refresh_tokens "
                "WHERE active_auth_session_id = :sid",
                sid=issue.session_id,
            )
        )[0][0]
        assert active == 0

    asyncio.run(scenario())


def test_csrf_bound_to_session(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """别人的或伪造的 CSRF 值被拒；读取 CSRF 不轮换令牌。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        _, issue = await _register_and_login(service, "csrf@example.com", "203.0.113.91")
        _, other = await _register_and_login(service, "csrf-other@example.com", "203.0.113.92")

        with pytest.raises(InvalidCsrf):
            await service.refresh(issue.refresh_token, other.csrf_token, "203.0.113.91")
        with pytest.raises(InvalidCsrf):
            await service.refresh(issue.refresh_token, "forged-value", "203.0.113.91")
        with pytest.raises(InvalidCsrf):
            await service.refresh(issue.refresh_token, "", "203.0.113.91")

        # 被拒的刷新没有消费令牌
        rows = await _token_rows(migrated_mysql_database, issue.session_id)
        assert [consumed for _, consumed in rows] == [None]

        # 读取 CSRF 不轮换：两次调用同值，且原令牌仍可正常轮换
        first = await service.csrf_for_refresh(issue.refresh_token)
        second = await service.csrf_for_refresh(issue.refresh_token)
        assert first == second == issue.csrf_token
        assert len(await _token_rows(migrated_mysql_database, issue.session_id)) == 1

        rotated = await service.refresh(issue.refresh_token, first, "203.0.113.91")
        assert rotated.session_id == issue.session_id

        # 未知 Cookie 取不到 CSRF；退出同样要匹配的 CSRF
        with pytest.raises(InvalidCredentials):
            await service.csrf_for_refresh(secrets.token_urlsafe(32))
        with pytest.raises(InvalidCsrf):
            await service.logout(rotated.refresh_token, other.csrf_token)
        state = await _session_state(migrated_mysql_database, issue.session_id)
        assert state["revoked_at"] is None

    asyncio.run(scenario())


def test_logout_revokes_access_immediately(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """退出后旧 JWT 立即失效，刷新令牌同时作废。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        view, issue = await _register_and_login(service, "logout@example.com", "203.0.113.99")
        assert (await service.verify(issue.access_token)).subject == view.id

        await service.logout(issue.refresh_token, issue.csrf_token)

        with pytest.raises(InvalidAccessToken):
            await service.verify(issue.access_token)
        with pytest.raises(InvalidCredentials):
            await service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.99")
        with pytest.raises(InvalidCredentials):
            await service.csrf_for_refresh(issue.refresh_token)

        state = await _session_state(migrated_mysql_database, issue.session_id)
        assert state["revoked_at"] is not None
        assert state["revoke_reason"] == "logout"
        active = (
            await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_refresh_tokens "
                "WHERE active_auth_session_id = :sid AND revoked_at IS NULL",
                sid=issue.session_id,
            )
        )[0][0]
        assert active == 0

    asyncio.run(scenario())


async def _session_state(database_url: str, session_id: str) -> dict:
    rows = await _query(
        database_url,
        "SELECT revoked_at, revoke_reason FROM identity_auth_sessions WHERE id = :sid",
        sid=session_id,
    )
    assert rows, f"登录会话 {session_id} 不存在"
    return {"revoked_at": rows[0][0], "revoke_reason": rows[0][1]}


async def _token_rows(database_url: str, session_id: str) -> list[tuple]:
    return await _query(
        database_url,
        "SELECT token_hash, consumed_at FROM identity_refresh_tokens "
        "WHERE auth_session_id = :sid",
        sid=session_id,
    )


async def _set_session_expiry(database_url: str, session_id: str, moment) -> None:
    factory = create_session_factory(database_url)
    async with factory() as db:
        session = await db.get(AuthSession, session_id)
        session.expires_at = moment
        await db.commit()


async def _delete_user(database_url: str, user_id: str) -> None:
    factory = create_session_factory(database_url)
    async with factory() as db:
        await db.execute(
            text("DELETE FROM identity_refresh_tokens WHERE auth_session_id IN "
                 "(SELECT id FROM identity_auth_sessions WHERE user_id = :uid)"),
            {"uid": user_id},
        )
        await db.execute(
            text("DELETE FROM identity_auth_sessions WHERE user_id = :uid"), {"uid": user_id}
        )
        await db.execute(text("DELETE FROM identity_users WHERE id = :uid"), {"uid": user_id})
        await db.commit()


# ---------- 受控演示密码重置 ----------

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load_reset_script():
    import importlib.util

    path = SCRIPTS_DIR / "reset_demo_password.py"
    spec = importlib.util.spec_from_file_location("reset_demo_password", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_controlled_reset_revokes_all_sessions(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """受控重置后两枚旧 JWT 与两枚刷新令牌全部失效，新密码可登录、旧密码不可。"""
    old_password = "a-long-enough-demo-password"
    new_password = "another-long-enough-password"

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        view = await service.register_demo("reset@example.com", old_password, "203.0.113.101")
        first = await service.login("reset@example.com", old_password, "203.0.113.101")
        second = await service.login("reset@example.com", old_password, "203.0.113.102")
        assert first.session_id != second.session_id
        assert (await service.verify(first.access_token)).subject == view.id
        assert (await service.verify(second.access_token)).subject == view.id

        await service.reset_demo_password(view.id, new_password)

        for issue in (first, second):
            with pytest.raises(InvalidAccessToken):
                await service.verify(issue.access_token)
            with pytest.raises(InvalidCredentials):
                await service.refresh(issue.refresh_token, issue.csrf_token, "203.0.113.101")
            with pytest.raises(InvalidCredentials):
                await service.csrf_for_refresh(issue.refresh_token)
            state = await _session_state(migrated_mysql_database, issue.session_id)
            assert state["revoked_at"] is not None
            assert state["revoke_reason"] == "password_reset"

        # 活动刷新令牌槽位全部释放
        active = (
            await _query(
                migrated_mysql_database,
                "SELECT COUNT(*) FROM identity_refresh_tokens t "
                "JOIN identity_auth_sessions s ON s.id = t.auth_session_id "
                "WHERE s.user_id = :uid AND t.active_auth_session_id IS NOT NULL",
                uid=view.id,
            )
        )[0][0]
        assert active == 0

        # 新密码可登录、旧密码不可，且哈希已更新
        with pytest.raises(InvalidCredentials):
            await service.login("reset@example.com", old_password, "203.0.113.103")
        reopened = await service.login("reset@example.com", new_password, "203.0.113.104")
        assert (await service.verify(reopened.access_token)).subject == view.id

        stored = await _load_user(migrated_mysql_database, view.id)
        assert stored.password_hash != new_password
        assert verify_password(new_password, stored.password_hash) is True
        assert verify_password(old_password, stored.password_hash) is False

    asyncio.run(scenario())


def test_reset_denied_in_production(
    migrated_mysql_database: str, clean_redis: str, identity_codec
):
    """正式环境与无效用户 ID 都不修改账号。"""

    async def scenario():
        service = await _build_service(migrated_mysql_database, clean_redis, identity_codec)
        view = await service.register_demo("keep@example.com", DEMO_PASSWORD, "203.0.113.111")
        before = await _load_user(migrated_mysql_database, view.id)

        production = await _build_service(
            migrated_mysql_database,
            clean_redis,
            identity_codec,
            environment="production",
            demo_mode=False,
        )
        with pytest.raises(DemoModeDisabled):
            await production.reset_demo_password(view.id, "another-long-enough-password")

        # 无效用户 ID / 非演示账号
        with pytest.raises(UnknownAccount):
            await service.reset_demo_password(str(uuid.uuid4()), "another-long-enough-password")
        factory = create_session_factory(migrated_mysql_database)
        other_source = await _create_user(
            factory, "not-demo@example.com", source="password", password_hash=before.password_hash
        )
        with pytest.raises(UnknownAccount):
            await service.reset_demo_password(other_source.id, "another-long-enough-password")

        # 不合规的新密码同样被拒，账号保持不变
        with pytest.raises(PasswordPolicyError):
            await service.reset_demo_password(view.id, "short")

        after = await _load_user(migrated_mysql_database, view.id)
        assert after.password_hash == before.password_hash
        # 原密码仍然可以登录，说明账号没被改动
        reopened = await service.login("keep@example.com", DEMO_PASSWORD, "203.0.113.112")
        assert (await service.verify(reopened.access_token)).subject == view.id

    asyncio.run(scenario())


def test_reset_script_takes_no_password_argument_and_hides_secrets(monkeypatch, capsys):
    """脚本只从交互式输入读密码，失败时不回显密码或数据库 URL。"""
    secret = "a-very-secret-demo-password"
    script = _load_reset_script()

    # 不接受命令行密码参数（argparse 会把参数原样回显，这里用占位值）
    with pytest.raises(SystemExit):
        script.parse_args(
            ["--user-id", "00000000-0000-0000-0000-000000000000", "--password", "placeholder"]
        )
    parsed = script.parse_args(["--user-id", "00000000-0000-0000-0000-000000000000"])
    assert parsed.user_id == "00000000-0000-0000-0000-000000000000"
    capsys.readouterr()

    # 交互式读取：源码里必须用 getpass，而不是明文回显
    source = (SCRIPTS_DIR / "reset_demo_password.py").read_text(encoding="utf-8")
    assert "getpass" in source
    assert "--password" not in source

    # 失败路径只输出脱敏原因
    async def failing_reset(user_id: str, new_password: str) -> None:
        raise IdentityConfigurationError(
            "未配置身份签名私钥：请设置 ECOM_IDENTITY_PRIVATE_KEY_PATH"
        )

    monkeypatch.setattr(script, "reset_demo_password", failing_reset)
    exit_code = script.main(
        ["--user-id", "00000000-0000-0000-0000-000000000000"],
        password_reader=lambda: secret,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 1
    assert secret not in output
    assert "mysql+asyncmy://" not in output
    assert "ECOM_IDENTITY_PRIVATE_KEY_PATH" in output

    # 交互式读取：两次不一致按取消处理，一致才返回
    class StubGetpass:
        def __init__(self, answers):
            self.answers = list(answers)

        def getpass(self, prompt=""):
            return self.answers.pop(0)

    monkeypatch.setattr(script, "getpass", StubGetpass([secret, "different-long-password"]))
    with pytest.raises(ValueError):
        script.prompt_new_password()
    monkeypatch.setattr(script, "getpass", StubGetpass([secret, secret]))
    assert script.prompt_new_password() == secret

    # 取消时不执行重置，也不回显密码
    def cancelling_reader() -> str:
        raise ValueError("两次输入不一致")

    assert (
        script.main(
            ["--user-id", "00000000-0000-0000-0000-000000000000"],
            password_reader=cancelling_reader,
        )
        == 1
    )
    assert secret not in capsys.readouterr().err
