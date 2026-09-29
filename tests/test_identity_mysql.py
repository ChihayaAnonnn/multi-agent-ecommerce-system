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
from identity.models import AuthSession, IdentityUser, RefreshToken
from tests.conftest import downgrade_to, read_schema, upgrade_to

IDENTITY_TABLES = {"identity_users", "identity_auth_sessions", "identity_refresh_tokens"}


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
