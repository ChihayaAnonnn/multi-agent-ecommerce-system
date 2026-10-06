"""MySQL/Redis 集成测试共用的夹具与 Alembic 辅助函数。

这些测试用真实的 MySQL 库与 Redis 验证迁移、约束、事务和限流，不能用内存桩替代。
测试库通过 ECOM_TEST_DATABASE_URL 显式指定，且必须与 ECOM_DATABASE_URL 指向不同的库；
隔离 Redis 通过 ECOM_TEST_REDIS_URL 指定，夹具会在用例前后清空它。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ecom.config import get_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def database_name(database_url: str) -> str:
    """Return the schema/database name a SQLAlchemy URL points at."""
    return urlparse(database_url).path.lstrip("/")


def alembic_config(database_url: str) -> Config:
    """Build an Alembic config that runs against the given database."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to(database_url: str, revision: str) -> None:
    command.upgrade(alembic_config(database_url), revision)


def upgrade_to_head(database_url: str) -> None:
    upgrade_to(database_url, "head")


def downgrade_to(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


def downgrade_to_base(database_url: str) -> None:
    downgrade_to(database_url, "base")


async def _read_schema(database_url: str) -> dict[str, Any]:
    """Read tables, unique constraints and columns from information_schema."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            tables = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
            )
            uniques = await connection.execute(
                text(
                    "SELECT tc.table_name, GROUP_CONCAT(kcu.column_name ORDER BY kcu.ordinal_position) "
                    "AS columns FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON kcu.constraint_name = tc.constraint_name "
                    " AND kcu.table_schema = tc.table_schema "
                    " AND kcu.table_name = tc.table_name "
                    "WHERE tc.constraint_type = 'UNIQUE' AND tc.table_schema = DATABASE() "
                    "GROUP BY tc.table_name, tc.constraint_name"
                )
            )
            columns = await connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE()"
                )
            )
            table_rows = [row[0] for row in tables]
            unique_rows = [(row[0], row[1].split(",")) for row in uniques]
            column_rows = [(row[0], row[1]) for row in columns]
    finally:
        await engine.dispose()

    schema: dict[str, Any] = {"tables": set(table_rows), "uniques": set(), "columns": {}}
    for table_name, unique_columns in unique_rows:
        schema["uniques"].add((table_name, tuple(sorted(unique_columns))))
    for table_name, column_name in column_rows:
        schema["columns"].setdefault(table_name, set()).add(column_name)
    return schema


def read_schema(database_url: str) -> dict[str, Any]:
    """Synchronously read the schema of the given database."""
    import asyncio

    return asyncio.run(_read_schema(database_url))


def test_database_url_from_settings() -> str:
    """Resolve the isolated test database URL, or an empty string when unset.

    优先读环境变量，其次读本地开发覆盖文件 .env.local（已被 .gitignore 忽略）。
    该变量刻意不进入 config.Settings，避免测试库配置影响应用启动。
    """
    from_env = os.environ.get("ECOM_TEST_DATABASE_URL")
    if from_env:
        return from_env
    local_env = REPO_ROOT / ".env.local"
    if local_env.exists():
        from dotenv import dotenv_values

        return dotenv_values(local_env).get("ECOM_TEST_DATABASE_URL") or ""
    return ""


@pytest.fixture(scope="session")
def mysql_test_database_url(request: pytest.FixtureRequest) -> str:
    """Resolve and validate the isolated MySQL test database URL."""
    url = (
        os.environ.get("ECOM_TEST_DATABASE_URL", "")
        if request.config.getoption("--require-integration-services")
        else test_database_url_from_settings()
    )
    if not url:
        pytest.skip(
            "ECOM_TEST_DATABASE_URL 未配置：MySQL 迁移、约束与事务测试需要独立测试库，"
            "未配置时这些测试不会执行，也不能据此认为数据库行为已验证。"
        )
    if not url.startswith("mysql+asyncmy://"):
        raise RuntimeError(
            f"ECOM_TEST_DATABASE_URL 必须是 mysql+asyncmy:// URL，当前值为 {url.split('://')[0]}://..."
        )
    configured = get_settings().database_url
    if configured and database_name(configured) == database_name(url):
        raise RuntimeError(
            "ECOM_TEST_DATABASE_URL 必须指向与 ECOM_DATABASE_URL 不同的库，"
            f"两者当前都是 {database_name(url)}"
        )
    return url


@pytest.fixture
def migrated_mysql_database(mysql_test_database_url: str) -> str:
    """Provide an empty test database upgraded to head, downgraded afterwards."""
    downgrade_to_base(mysql_test_database_url)
    upgrade_to_head(mysql_test_database_url)
    yield mysql_test_database_url
    downgrade_to_base(mysql_test_database_url)


def test_redis_url_from_settings() -> str:
    """Resolve the isolated test Redis URL, or an empty string when unset.

    与测试库同样刻意不进入 config.Settings，避免测试实例影响应用启动。
    """
    from_env = os.environ.get("ECOM_TEST_REDIS_URL")
    if from_env:
        return from_env
    local_env = REPO_ROOT / ".env.local"
    if local_env.exists():
        from dotenv import dotenv_values

        return dotenv_values(local_env).get("ECOM_TEST_REDIS_URL") or ""
    return ""


@pytest.fixture(scope="session")
def redis_test_url(request: pytest.FixtureRequest) -> str:
    """Resolve and validate the isolated Redis instance used by rate-limit tests."""
    url = (
        os.environ.get("ECOM_TEST_REDIS_URL", "")
        if request.config.getoption("--require-integration-services")
        else test_redis_url_from_settings()
    )
    if not url:
        pytest.skip(
            "ECOM_TEST_REDIS_URL 未配置：Redis 限流测试需要独立测试实例，"
            "未配置时这些测试不会执行，也不能据此认为限流行为已验证。"
        )
    if not url.startswith("redis://"):
        raise RuntimeError(
            f"ECOM_TEST_REDIS_URL 必须是 redis:// URL，当前值为 {url.split('://')[0]}://..."
        )
    return url


# 身份模块只写这一个前缀的键；夹具据此清理，绝不做 FLUSHDB。
RATE_LIMIT_KEY_PATTERN = "identity:rl:*"


async def _clear_identity_keys(url: str) -> None:
    """只删除身份限流自己的键。

    这个 Redis 可能与别的服务共用同一个 DB（本机就是如此），`FLUSHDB`
    会连带清掉别人正在用的状态，因此按前缀精确删除。
    每次调用都在自己的事件循环里建立并关闭连接，避免跨循环复用。
    """
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    try:
        keys = [
            key async for key in client.scan_iter(match=RATE_LIMIT_KEY_PATTERN, count=200)
        ]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


@pytest.fixture
def clean_redis(redis_test_url: str) -> str:
    """给出一个用例前后都清掉身份限流键的 Redis URL。

    返回 URL 而不是连接对象：用例在自己的事件循环内建立连接，避免跨循环复用。
    """
    asyncio.run(_clear_identity_keys(redis_test_url))
    yield redis_test_url
    asyncio.run(_clear_identity_keys(redis_test_url))


def integration_markers(fixture_names: list[str]) -> set[str]:
    """按 fixture 的传递依赖区分真实服务测试，而非按文件名猜测。"""
    markers = set()
    if {"mysql_test_database_url", "migrated_mysql_database"} & set(fixture_names):
        markers.add("mysql")
    if {"redis_test_url", "clean_redis"} & set(fixture_names):
        markers.add("redis")
    return markers


def pytest_addoption(parser) -> None:
    parser.addoption("--layering-artifacts", metavar="PATH", default=None)
    parser.addoption("--require-integration-services", action="store_true")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items) -> None:
    for item in items:
        for marker in integration_markers(item.fixturenames):
            item.add_marker(getattr(pytest.mark, marker))
        item.user_properties.append(("node_id", item.nodeid))


def pytest_collection_finish(session) -> None:
    import json

    config = session.config
    if config.getoption("--require-integration-services"):
        categories = {
            marker for item in session.items
            for marker in integration_markers(item.fixturenames)
        }
        for category, variable in (
            ("mysql", "ECOM_TEST_DATABASE_URL"), ("redis", "ECOM_TEST_REDIS_URL")
        ):
            if category in categories and not os.environ.get(variable):
                raise pytest.UsageError(f"{variable} must be explicitly configured")
    target = config.getoption("--layering-artifacts")
    if target:
        directory = pathlib.Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {"tests": [
            {"node_id": item.nodeid,
             "markers": sorted(integration_markers(item.fixturenames))}
            for item in session.items
        ]}
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
