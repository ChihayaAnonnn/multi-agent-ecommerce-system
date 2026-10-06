"""Async Milvus connection for vector operations."""

from pymilvus import AsyncMilvusClient as PyAsyncMilvusClient


class MilvusClient:
    def __init__(self, host: str, port: int, collection_name: str):
        self.collection_name = collection_name
        self.client = PyAsyncMilvusClient(uri=f"http://{host}:{port}")

    async def ping(self) -> list[str]:
        return await self.client.list_collections()

    async def close(self) -> None:
        await self.client.close()
