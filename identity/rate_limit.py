"""基于 Redis 的原子限流。

限流键分维度独立计数（规范化邮箱、客户端 IP、登录会话），用单条 Lua 脚本
把 `INCR` 与首次 `EXPIRE` 合并成一次原子操作，避免并发下漏设过期时间。
Redis 不可用时一律拒绝服务（`IdentityDependencyError`），不因为「读不到计数」
就放行——否则故障即等于关闭限流。
"""

from __future__ import annotations

import hashlib

from identity.types import IdentityDependencyError, RateLimited

# 邮箱不进 Redis 键：用截断摘要区分身份，避免把登录名当作键名长期保存。
EMAIL_KEY_DIGEST_LENGTH = 32

# 计数与过期时间在同一次往返内完成：只有首次自增才设置过期时间。
_COUNTER_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""

# 只读检查：用于「失败次数是否已达上限」，不消耗计数。
_READ_SCRIPT = """
local value = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('TTL', KEYS[1])
return {value, ttl}
"""


def _email_digest(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:EMAIL_KEY_DIGEST_LENGTH]


class RedisRateLimiter:
    """按邮箱/IP/会话维度的固定窗口限流。"""

    def __init__(
        self,
        redis_client,
        *,
        login_email_limit: int = 5,
        login_ip_limit: int = 30,
        register_ip_limit: int = 3,
        refresh_session_limit: int = 30,
        login_window_seconds: int = 900,
        register_window_seconds: int = 3600,
        refresh_window_seconds: int = 60,
        key_prefix: str = "identity:rl",
    ):
        self._redis = redis_client
        self._login_email_limit = login_email_limit
        self._login_ip_limit = login_ip_limit
        self._register_ip_limit = register_ip_limit
        self._refresh_limit = refresh_session_limit
        self._login_window = login_window_seconds
        self._register_window = register_window_seconds
        self._refresh_window = refresh_window_seconds
        self._prefix = key_prefix
        self._counter = redis_client.register_script(_COUNTER_SCRIPT)
        self._reader = redis_client.register_script(_READ_SCRIPT)

    # ---------- 键 ----------

    def _key(self, *parts: str) -> str:
        return ":".join((self._prefix, *parts))

    def _login_email_key(self, email: str) -> str:
        return self._key("login", "email", _email_digest(email))

    def _login_ip_key(self, client_ip: str) -> str:
        return self._key("login", "ip", client_ip)

    def _register_ip_key(self, client_ip: str) -> str:
        return self._key("register", "ip", client_ip)

    def _refresh_key(self, session_id: str | None, client_ip: str) -> str:
        if session_id:
            return self._key("refresh", "session", session_id)
        # 未知 Cookie 没有会话可归属，退化为按客户端 IP 限流
        return self._key("refresh", "ip", client_ip)

    # ---------- 原语 ----------

    async def _increment_and_enforce(self, key: str, limit: int, window: int) -> None:
        """自增一次并检查上限；超限抛 `RateLimited`。"""
        try:
            count, ttl = await self._counter(keys=[key], args=[window])
        except Exception as exc:  # redis 异常类型较多，一律视为依赖故障
            raise IdentityDependencyError("限流服务不可用") from exc
        if int(count) > limit:
            raise RateLimited(max(1, int(ttl)))

    async def _enforce_existing(self, key: str, limit: int, window: int) -> None:
        """只检查已有失败计数，不消耗计数。"""
        try:
            count, ttl = await self._reader(keys=[key], args=[window])
        except Exception as exc:
            raise IdentityDependencyError("限流服务不可用") from exc
        if int(count) >= limit:
            raise RateLimited(max(1, int(ttl)))

    async def _record(self, key: str, window: int) -> None:
        try:
            await self._counter(keys=[key], args=[window])
        except Exception as exc:
            raise IdentityDependencyError("限流服务不可用") from exc

    # ---------- 对外方法 ----------

    async def check_register(self, client_ip: str) -> None:
        """注册按 IP 计数：第 N+1 次尝试被拒。"""
        await self._increment_and_enforce(
            self._register_ip_key(client_ip), self._register_ip_limit, self._register_window
        )

    async def check_login(self, email: str, client_ip: str) -> None:
        """登录前检查失败次数；邮箱与 IP 任一超限即拒绝。"""
        await self._enforce_existing(
            self._login_email_key(email), self._login_email_limit, self._login_window
        )
        await self._enforce_existing(
            self._login_ip_key(client_ip), self._login_ip_limit, self._login_window
        )

    async def record_login_failure(self, email: str, client_ip: str) -> None:
        """登录失败后计一次；邮箱与 IP 两个维度同时累加。"""
        await self._record(self._login_email_key(email), self._login_window)
        await self._record(self._login_ip_key(client_ip), self._login_window)

    async def check_refresh(self, session_id: str | None, client_ip: str) -> None:
        """刷新按会话计数；Cookie 不可用时按 IP 计数。"""
        await self._increment_and_enforce(
            self._refresh_key(session_id, client_ip), self._refresh_limit, self._refresh_window
        )
