"""MySQL 集成测试共用的夹具与 Alembic 辅助函数。

这些测试用真实的 MySQL 库验证迁移、约束和事务，不能用内存桩替代。
测试库通过 ECOM_TEST_DATABASE_URL 显式指定，且必须与 ECOM_DATABASE_URL 指向不同的库。
"""

from __future__ import annotations

import os
import pathlib
from typing import Any
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config import get_settings

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
def mysql_test_database_url() -> str:
    """Resolve and validate the isolated MySQL test database URL."""
    url = test_database_url_from_settings()
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
