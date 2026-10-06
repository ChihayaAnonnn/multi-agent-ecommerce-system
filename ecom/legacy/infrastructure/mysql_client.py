"""Async MySQL engine and session factory for business data."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class MySQLClient:
    def __init__(self, database_url: str):
        if not database_url.startswith("mysql+asyncmy://"):
            raise ValueError("MySQLClient requires a mysql+asyncmy:// URL")
        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
