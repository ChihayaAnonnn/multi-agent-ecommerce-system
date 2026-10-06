"""限流测试共用的维度上限与限流器构造。

拆分 `test_identity_rate_limit.py` 时抽出的辅助：纯桩故障用例与 Redis 集成用例
必须使用同一组上限，复制两份会让阈值漂移后两边同时"通过"。
"""

from __future__ import annotations

from redis.asyncio import Redis

from ecom.modules.identity.rate_limit import RedisRateLimiter

LOGIN_EMAIL_LIMIT = 5
LOGIN_IP_LIMIT = 30
REGISTER_IP_LIMIT = 3
REFRESH_LIMIT = 30


def build_limiter(client: Redis) -> RedisRateLimiter:
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
