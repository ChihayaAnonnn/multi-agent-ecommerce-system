import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class RedisClientTests(unittest.TestCase):
    def test_connect_ping_and_close(self):
        from legacy.infrastructure.redis_client import RedisClient

        raw = MagicMock()
        raw.ping = AsyncMock(return_value=True)
        raw.aclose = AsyncMock()
        with patch(
            "legacy.infrastructure.redis_client.Redis.from_url",
            return_value=raw,
        ) as factory:
            client = RedisClient("redis://localhost:6379/0")
            self.assertIs(client.client, raw)
            self.assertTrue(asyncio.run(client.ping()))
            asyncio.run(client.close())

        factory.assert_called_once_with("redis://localhost:6379/0", decode_responses=True)
        raw.aclose.assert_awaited_once()


class MySQLClientTests(unittest.TestCase):
    def test_rejects_non_mysql_url(self):
        from legacy.infrastructure.mysql_client import MySQLClient

        with self.assertRaisesRegex(ValueError, "mysql\\+asyncmy"):
            MySQLClient("sqlite:///example.db")

    def test_ping_and_dispose(self):
        from legacy.infrastructure.mysql_client import MySQLClient

        connection = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=connection)
        context.__aexit__ = AsyncMock(return_value=False)
        engine = MagicMock()
        engine.connect.return_value = context
        engine.dispose = AsyncMock()
        url = "mysql+asyncmy://user:password@localhost:3306/ecommerce"

        with patch(
            "legacy.infrastructure.mysql_client.create_async_engine",
            return_value=engine,
        ) as factory:
            client = MySQLClient(url)
            asyncio.run(client.ping())
            asyncio.run(client.close())

        factory.assert_called_once_with(url, pool_pre_ping=True)
        connection.execute.assert_awaited_once()
        engine.dispose.assert_awaited_once()


class MilvusClientTests(unittest.TestCase):
    def test_ping_and_close(self):
        from legacy.infrastructure.milvus_client import MilvusClient

        raw = MagicMock()
        raw.list_collections = AsyncMock(return_value=["products"])
        raw.close = AsyncMock()
        with patch(
            "legacy.infrastructure.milvus_client.PyAsyncMilvusClient",
            return_value=raw,
        ) as factory:
            client = MilvusClient("localhost", 19530, "product_embeddings")
            self.assertIs(client.client, raw)
            self.assertEqual(client.collection_name, "product_embeddings")
            self.assertEqual(asyncio.run(client.ping()), ["products"])
            asyncio.run(client.close())

        factory.assert_called_once_with(uri="http://localhost:19530")
        raw.list_collections.assert_awaited_once()
        raw.close.assert_awaited_once()
