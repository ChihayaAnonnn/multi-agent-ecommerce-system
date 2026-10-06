"""异步数据库会话工厂。

只在创建会话时校验 URL，因此未配置数据库时应用仍可启动，旧推荐接口不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MYSQL_URL_PREFIX = "mysql+asyncmy://"


class DatabaseConfigurationError(ValueError):
    """数据库缺失或 URL 协议错误；保持兼容 ValueError。"""


@dataclass(frozen=True)
class DatabaseResources:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


def create_database(database_url: str) -> DatabaseResources:
    """为 MySQL 创建异步会话工厂。

    拒绝空值和非 MySQL URL；错误信息不回显完整 URL，避免泄露口令。
    """
    if not database_url:
        raise DatabaseConfigurationError(
            "数据库未配置：请将 ECOM_DATABASE_URL 设置为 mysql+asyncmy:// URL"
        )
    if not database_url.startswith(MYSQL_URL_PREFIX):
        scheme = database_url.split("://", 1)[0]
        raise DatabaseConfigurationError(
            f"数据库 URL 必须以 {MYSQL_URL_PREFIX} 开头，当前为 {scheme}://"
        )

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return DatabaseResources(engine, async_sessionmaker(engine, expire_on_commit=False))


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """为尚未迁移的工具保留原会话工厂入口。"""
    return create_database(database_url).session_factory
