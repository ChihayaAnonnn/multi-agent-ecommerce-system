"""身份领域 MySQL 集成测试：迁移约束、账号写入与令牌轮换。

这些用例必须连真实 MySQL 执行；`ECOM_TEST_DATABASE_URL` 未配置时跳过，
跳过不构成「数据库行为已验证」。
"""

from __future__ import annotations

import asyncio
import hashlib
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
from identity.passwords import hash_password
from identity.rate_limit import RedisRateLimiter
from identity.service import (
    DEFAULT_SESSION_TTL_SECONDS,
    IdentityService,
    hash_refresh_token,
    normalize_email,
)
from identity.tokens import TokenCodec
from identity.types import (
    DemoModeDisabled,
    EmailAlreadyRegistered,
    IdentityConfigurationError,
    InvalidCredentials,
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
