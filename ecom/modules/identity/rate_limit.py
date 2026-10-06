"""基于 Redis 的原子限流。

限流键分维度独立计数（规范化邮箱、客户端 IP、登录会话），用单条 Lua 脚本
把「计数」与「判定」合成一次原子操作：先检查后计数会让并发请求同时读到
未超限的旧值（TOCTOU），一起放行并突破上限。登录成功后把这次占用还回去，
正常用户不消耗失败配额。

Redis 不可用时一律拒绝服务（`IdentityDependencyError`），不因为「读不到计数」
就放行——否则故障即等于关闭限流。
"""

from __future__ import annotations

import hashlib

from ecom.modules.identity.types import IdentityDependencyError, RateLimited

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

# 释放一次占用：登录成功后把本次占用还回去，正常用户不消耗失败配额。
# 只减不加，且归零即删；不会把别人累计的失败一起抹掉。
_RELEASE_SCRIPT = """
local value = tonumber(redis.call('GET', KEYS[1]) or '0')
if value <= 0 then
  redis.call('DEL', KEYS[1])
  return 0
end
local remaining = redis.call('DECR', KEYS[1])
if remaining <= 0 then
  redis.call('DEL', KEYS[1])
end
return remaining
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
        self._releaser = redis_client.register_script(_RELEASE_SCRIPT)

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

    async def _record(self, key: str, window: int) -> None:
        try:
            await self._counter(keys=[key], args=[window])
        except Exception as exc:
            raise IdentityDependencyError("限流服务不可用") from exc

    async def _release(self, key: str) -> None:
        try:
            await self._releaser(keys=[key], args=[])
        except Exception as exc:
            raise IdentityDependencyError("限流服务不可用") from exc

    # ---------- 对外方法 ----------

    async def check_register(self, client_ip: str) -> None:
        """注册按 IP 计数：第 N+1 次尝试被拒。"""
        await self._increment_and_enforce(
            self._register_ip_key(client_ip), self._register_ip_limit, self._register_window
        )

    async def check_login(self, email: str, client_ip: str) -> None:
        """占用一次登录尝试配额并检查上限；邮箱与 IP 任一超限即拒绝。

        「计数」与「判定」在同一条 Lua 脚本里完成。若先读计数再在验证失败后补记，
        并发请求会同时读到未超限的旧值（TOCTOU），一起放行并突破失败上限。
        登录成功后调用 `clear_login_attempts` 把这次占用还回去。
        """
        await self._increment_and_enforce(
            self._login_email_key(email), self._login_email_limit, self._login_window
        )
        await self._increment_and_enforce(
            self._login_ip_key(client_ip), self._login_ip_limit, self._login_window
        )

    async def clear_login_attempts(self, email: str, client_ip: str) -> None:
        """登录成功后释放本次占用：成功登录不消耗失败配额。"""
        await self._release(self._login_email_key(email))
        await self._release(self._login_ip_key(client_ip))

    async def record_login_failure(self, email: str, client_ip: str) -> None:
        """补记一次失败，与 `check_login` 共用同一计数器。

        正常登录流程在 `check_login` 时已经占用配额，不需要再调用；
        这里留给没有先占用的调用方（审计补记、运维脚本）。
        """
        await self._record(self._login_email_key(email), self._login_window)
        await self._record(self._login_ip_key(client_ip), self._login_window)

    async def check_refresh(self, session_id: str | None, client_ip: str) -> None:
        """刷新按会话计数；Cookie 不可用时按 IP 计数。"""
        await self._increment_and_enforce(
            self._refresh_key(session_id, client_ip), self._refresh_limit, self._refresh_window
        )
