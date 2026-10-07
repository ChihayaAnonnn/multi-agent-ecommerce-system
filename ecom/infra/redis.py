"""Redis 客户端适配器。

与 `ecom/infra/db.py` 的 `create_database` 对称：连接是惰性的，未配置或不可达
时应用仍可启动，由使用方的降级路径处理；只由组合根构造。
"""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    """按配置创建异步 Redis 客户端；调用方负责在退出时关闭自己创建的那一个。"""
    return Redis.from_url(redis_url, decode_responses=True)
