"""Async Redis connection for feature storage."""

from redis.asyncio import Redis


class RedisClient:
    def __init__(self, url: str):
        self.client = Redis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()
