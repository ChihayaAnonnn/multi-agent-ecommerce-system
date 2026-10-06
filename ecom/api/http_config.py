"""认证 HTTP 配置与可信代理解析，不由身份业务层读取部署设置。"""

import ipaddress
from dataclasses import dataclass, field
from functools import lru_cache

from ecom.modules.identity.types import IdentityConfigurationError

# 认证域共享的请求/响应头常量：中间件与路由都要用，集中在此避免两处重复定义。
# 刷新 Cookie 名默认值见 IdentityHttpConfig：
# `__Host-` 前缀要求 Secure、Path=/ 且不带 Domain。
CSRF_HEADER_NAME = 'X-CSRF-Token'
NO_STORE = {'Cache-Control': 'no-store'}
UNAUTHORIZED_HEADERS = {'WWW-Authenticate': 'Bearer'}


@dataclass(frozen=True)
class IdentityHttpConfig:
    """来源白名单、可信代理与刷新 Cookie 配置。"""

    allowed_origins: tuple[str, ...] = ()
    trusted_proxy_networks: tuple[str, ...] = ()
    refresh_cookie_name: str = '__Host-ecom_refresh'
    session_ttl_seconds: int = 2592000
    extra: dict = field(default_factory=dict)


def parse_http_list(value: str) -> tuple[str, ...]:
    """切分 HTTP 白名单配置，去空白、去空项并保持顺序。"""
    return tuple(item.strip() for item in value.split(',') if item.strip())


@lru_cache(maxsize=64)
def parse_trusted_networks(
    networks: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """无效 CIDR 配置直接失败，不静默忽略。"""
    parsed = []
    for entry in networks:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise IdentityConfigurationError(
                f'可信代理网段不是合法 CIDR：{entry}'
                '（ECOM_IDENTITY_TRUSTED_PROXY_NETWORKS）'
            ) from exc
    return tuple(parsed)
