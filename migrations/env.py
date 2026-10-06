"""Alembic 异步迁移环境。

数据库 URL 来源优先级：调用方注入的 sqlalchemy.url（测试库走这条路）> ECOM_DATABASE_URL / .env。
应用启动不会自动执行迁移，迁移只能通过 alembic 命令或测试显式触发。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import ecom.modules.cart.models  # noqa: F401
import ecom.modules.catalog.models  # noqa: F401  注册目录表到 metadata
import ecom.modules.checkout.models  # noqa: F401
import ecom.modules.events.models  # noqa: F401
import ecom.modules.identity.models  # noqa: F401  注册身份表到 metadata
import ecom.modules.inventory.models  # noqa: F401  注册库存表到 metadata
import ecom.modules.pricing.models  # noqa: F401  注册价格表到 metadata
import ecom.modules.sessions.models  # noqa: F401
from ecom.shared.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """取迁移目标库的 URL。"""
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured

    from ecom.config import get_settings

    url = get_settings().database_url
    if not url:
        raise RuntimeError(
            "未配置数据库：请设置 ECOM_DATABASE_URL，或为 alembic 注入 sqlalchemy.url"
        )
    return url


def run_migrations_offline() -> None:
    """生成离线 SQL，不连接数据库。"""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
