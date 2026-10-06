"""Redis 限流的纯桩用例：依赖不可用时必须拒绝服务。

不需要真实 Redis：目标地址是不可达端口，只验证失败关闭而不是放行。
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from ecom.modules.identity.types import IdentityDependencyError
from tests.support.rate_limit_helpers import build_limiter


def test_redis_failure_fails_closed():
    """Redis 不可用时拒绝服务，而不是因为没有计数就放行。"""

    async def scenario():
        broken = Redis.from_url(
            "redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.2
        )
        limiter = build_limiter(broken)

        for call in (
            limiter.check_register("198.51.100.7"),
            limiter.check_login("demo@example.com", "198.51.100.7"),
            limiter.record_login_failure("demo@example.com", "198.51.100.7"),
            limiter.check_refresh("session-id", "198.51.100.7"),
        ):
            with pytest.raises(IdentityDependencyError):
                await call

        await broken.aclose()

    asyncio.run(scenario())
