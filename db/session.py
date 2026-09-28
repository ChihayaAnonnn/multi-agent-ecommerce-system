"""异步数据库会话工厂。

只在创建会话时校验 URL，因此未配置数据库时应用仍可启动，旧推荐接口不受影响。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MYSQL_URL_PREFIX = "mysql+asyncmy://"


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """为 MySQL 创建异步会话工厂。

    拒绝空值和非 MySQL URL；错误信息不回显完整 URL，避免泄露口令。
    """
    if not database_url:
        raise ValueError(
            "数据库未配置：请将 ECOM_DATABASE_URL 设置为 mysql+asyncmy:// URL"
        )
    if not database_url.startswith(MYSQL_URL_PREFIX):
        scheme = database_url.split("://", 1)[0]
        raise ValueError(
            f"数据库 URL 必须以 {MYSQL_URL_PREFIX} 开头，当前为 {scheme}://"
        )

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
