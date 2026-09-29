"""身份领域的对外类型与错误。

后续购物域只消费 `Principal`，不直接拼主体键，也不读取演示注册细节。
错误类型集中在这里，避免 `tokens`/`rate_limit`/`service` 之间相互导入。
"""

from __future__ import annotations

import hashlib
import ipaddress
import struct
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

# 允许开放演示注册/登录的部署环境；其他环境一律拒绝 source=demo。
DEMO_ENVIRONMENTS = ("development", "test", "demo")

# 主体键编码版本；改变编码规则必须换前缀，避免新旧键混用。
PRINCIPAL_KEY_VERSION = b"ecom-principal-v1\x00"


@dataclass(frozen=True)
class IdentityHttpConfig:
    """认证端点需要的 HTTP 层配置（来源白名单、可信代理、Cookie 名与时效）。"""

    allowed_origins: tuple[str, ...] = ()
    trusted_proxy_networks: tuple[str, ...] = ()
    refresh_cookie_name: str = "__Host-ecom_refresh"
    session_ttl_seconds: int = 2592000
    extra: dict = field(default_factory=dict)


@lru_cache(maxsize=64)
def parse_trusted_networks(networks: tuple[str, ...]) -> tuple:
    """解析可信代理 CIDR；配置写错时直接报错，不静默忽略。"""
    parsed = []
    for entry in networks:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise IdentityConfigurationError(
                f"可信代理网段不是合法 CIDR：{entry}（ECOM_IDENTITY_TRUSTED_PROXY_NETWORKS）"
            ) from exc
    return tuple(parsed)


@dataclass(frozen=True)
class Principal:
    """一次已验证请求的主体身份。

    `principal_key` 是 `(issuer, subject)` 的无歧义稳定编码摘要，
    未来接入其他身份提供方时不会与同值 `subject` 串户。
    """

    issuer: str
    subject: str
    principal_key: str


def _length_prefixed(value: str) -> bytes:
    """4 字节大端长度前缀 + UTF-8 字节，保证不同分段不会拼出同一串。"""
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def principal_key_for(issuer: str, subject: str) -> str:
    digest = hashlib.sha256(
        PRINCIPAL_KEY_VERSION + _length_prefixed(issuer) + _length_prefixed(subject)
    )
    return digest.hexdigest()


def make_principal(issuer: str, user_id: str) -> Principal:
    return Principal(
        issuer=issuer, subject=user_id, principal_key=principal_key_for(issuer, user_id)
    )


@dataclass(frozen=True)
class UserView:
    """对外的账号视图；演示账号的邮箱一律标记为未验证。"""

    id: str
    email: str
    email_verified: bool
    source: str


@dataclass(frozen=True)
class AuthIssue:
    """一次登录或刷新的结果。

    `refresh_token` 是原始令牌，只在设置 Cookie 的 API 层短暂使用，
    不进日志、不入库。
    """

    access_token: str
    refresh_token: str
    csrf_token: str
    expires_at: datetime
    user_id: str
    session_id: str


class IdentityError(Exception):
    """身份模块错误基类。"""


class IdentityConfigurationError(IdentityError):
    """密钥、签发方或部署环境配置不合法；不回显密钥内容。"""


class IdentityDependencyError(IdentityError):
    """MySQL 或 Redis 等依赖不可用；调用方应返回 503，不得降级为匿名。"""


class InvalidAccessToken(IdentityError):
    """访问令牌不可信（签名、声明、时效或会话状态任一不通过）。"""


class InvalidCredentials(IdentityError):
    """登录/刷新凭证无效；对外不区分账号不存在、密码错误与账号禁用。"""


class InvalidCsrf(IdentityError):
    """CSRF 值缺失或不匹配；调用方应返回 403。"""


class DemoModeDisabled(IdentityError):
    """当前部署环境不允许演示注册或演示账号登录。"""


class EmailAlreadyRegistered(IdentityError):
    """邮箱已被注册（规范化后唯一）。"""


class RateLimited(IdentityError):
    """触发限流，调用方应返回 429 与 `Retry-After`。"""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"请求过于频繁，请在 {retry_after_seconds} 秒后重试")
        self.retry_after_seconds = retry_after_seconds
