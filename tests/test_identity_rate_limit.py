"""Redis 限流集成测试：维度、窗口、Retry-After 与故障拒绝。

需要隔离的 `ECOM_TEST_REDIS_URL`；未配置时跳过，跳过不构成「限流已验证」。
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from identity.rate_limit import RedisRateLimiter
from identity.service import normalize_email
from identity.types import IdentityDependencyError, RateLimited

LOGIN_EMAIL_LIMIT = 5
LOGIN_IP_LIMIT = 30
REGISTER_IP_LIMIT = 3
REFRESH_LIMIT = 30


def _limiter(client: Redis) -> RedisRateLimiter:
    return RedisRateLimiter(
        client,
        login_email_limit=LOGIN_EMAIL_LIMIT,
        login_ip_limit=LOGIN_IP_LIMIT,
        register_ip_limit=REGISTER_IP_LIMIT,
        refresh_session_limit=REFRESH_LIMIT,
        login_window_seconds=900,
        register_window_seconds=3600,
        refresh_window_seconds=60,
    )


def test_redis_limits_and_failure(clean_redis: str):
    """注册 3 次/小时、登录失败 5 次/邮箱与 30 次/IP、刷新 30 次/会话。"""

    async def scenario():
        client = Redis.from_url(clean_redis, decode_responses=True)
        limiter = _limiter(client)
        ip = "198.51.100.7"

        # 注册：第 4 次尝试被拒，且给出正数 Retry-After
        for _ in range(REGISTER_IP_LIMIT):
            await limiter.check_register(ip)
        with pytest.raises(RateLimited) as error:
            await limiter.check_register(ip)
        assert error.value.retry_after_seconds > 0
        # 其他 IP 不受影响
        await limiter.check_register("198.51.100.8")

        # 登录：同一规范化邮箱第 6 次尝试被拒（失败的尝试一直计入）
        email = normalize_email("  Demo@Example.COM  ")
        for _ in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(email, ip)
        with pytest.raises(RateLimited):
            await limiter.check_login(email, ip)
        # 大小写与空白等价写法共享同一计数器
        with pytest.raises(RateLimited):
            await limiter.check_login(normalize_email("demo@example.com "), "198.51.100.9")
        # 不同邮箱在同一 IP 上仍然可以尝试
        await limiter.check_login("someone-else@example.com", ip)

        # 成功登录释放占用：不消耗失败配额
        ok_email = normalize_email("ok@example.com")
        for _ in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(ok_email, "203.0.113.44")
            await limiter.clear_login_attempts(ok_email, "203.0.113.44")
        for _ in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(ok_email, "203.0.113.44")
        with pytest.raises(RateLimited):
            await limiter.check_login(ok_email, "203.0.113.44")

        # 登录：IP 维度 30 次后拒绝，即使换邮箱
        failing_ip = "203.0.113.55"
        for index in range(LOGIN_IP_LIMIT):
            await limiter.record_login_failure(f"user{index}@example.com", failing_ip)
        with pytest.raises(RateLimited):
            await limiter.check_login("fresh@example.com", failing_ip)

        # 刷新：同一会话第 31 次被拒；未知会话退化为按 IP 计数
        session_id = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
        for _ in range(REFRESH_LIMIT):
            await limiter.check_refresh(session_id, "203.0.113.77")
        with pytest.raises(RateLimited):
            await limiter.check_refresh(session_id, "203.0.113.77")
        await limiter.check_refresh("another-session-id", "203.0.113.77")
        await limiter.check_refresh(None, "203.0.113.77")

        # 所有键都带过期时间，不会永久占用
        keys = await client.keys("identity:rl:*")
        assert keys
        for key in keys:
            assert await client.ttl(key) > 0, key
        # 邮箱不进键名：键里不能出现明文邮箱
        assert not any("example.com" in key for key in keys)

        await client.aclose()

    asyncio.run(scenario())


def test_concurrent_logins_cannot_exceed_the_limit(clean_redis: str):
    """并发登录不能突破上限：计数与判定必须是同一次原子操作。

    先检查、失败后再补记时，N 个并发请求会同时读到未超限的旧值全部放行。
    """

    async def scenario():
        client = Redis.from_url(clean_redis, decode_responses=True)
        limiter = _limiter(client)
        email = normalize_email("race@example.com")
        ip = "198.51.100.31"

        results = await asyncio.gather(
            *[limiter.check_login(email, ip) for _ in range(LOGIN_EMAIL_LIMIT * 2)],
            return_exceptions=True,
        )
        allowed = [item for item in results if item is None]
        blocked = [item for item in results if isinstance(item, RateLimited)]
        assert len(allowed) == LOGIN_EMAIL_LIMIT, results
        assert len(blocked) == LOGIN_EMAIL_LIMIT, results
        for error in blocked:
            assert error.retry_after_seconds > 0

        # IP 维度同样原子：换邮箱也绕不过
        other_ip = "198.51.100.32"
        results = await asyncio.gather(
            *[
                limiter.check_login(f"user{index}@example.com", other_ip)
                for index in range(LOGIN_IP_LIMIT * 2)
            ],
            return_exceptions=True,
        )
        assert len([item for item in results if item is None]) == LOGIN_IP_LIMIT

        await client.aclose()

    asyncio.run(scenario())


def test_redis_failure_fails_closed():
    """Redis 不可用时拒绝服务，而不是因为没有计数就放行。"""

    async def scenario():
        broken = Redis.from_url(
            "redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.2
        )
        limiter = _limiter(broken)

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
