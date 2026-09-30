"""HTTP 层的主体依赖、客户端 IP 解析与认证域 CORS。

购物路由只依赖 `require_principal` 拿到的 `Principal`，不得读取请求体里的
`user_id`。身份依赖不可用时受保护端点返回 503，绝不降级为匿名或固定主体。
"""

from __future__ import annotations

import ipaddress

from fastapi import Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.runtime import get_runtime
from identity.service import IdentityService
from identity.types import (
    IdentityDependencyError,
    IdentityHttpConfig,
    InvalidAccessToken,
    Principal,
    parse_trusted_networks,
)

# 刷新 Cookie 名默认值见 identity.types.IdentityHttpConfig：
# `__Host-` 前缀要求 Secure、Path=/ 且不带 Domain。
CSRF_HEADER_NAME = "X-CSRF-Token"

BEARER_SCHEME = HTTPBearer(auto_error=False)

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}
NO_STORE = {"Cache-Control": "no-store"}


def is_trusted_proxy(address: str, networks: tuple[str, ...]) -> bool:
    if not address or not networks:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in parse_trusted_networks(networks))


def resolve_client_ip(request: Request, trusted_proxy_networks: tuple[str, ...]) -> str:
    """解析限流与审计用的客户端 IP。

    只有直接来源命中可信代理网段时才读取 `X-Forwarded-For`，并从右向左取
    最靠近服务端的非可信地址——右侧是可信代理追加的，左侧可以被客户端伪造。
    """
    direct = request.client.host if request.client else ""
    if not is_trusted_proxy(direct, trusted_proxy_networks):
        return direct

    header = request.headers.get("x-forwarded-for", "")
    if not header.strip():
        return direct
    for hop in reversed(header.split(",")):
        candidate = hop.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            # 无法判断的条目：保守地退回直连地址，避免用垃圾值轮换限流键
            return direct
        if not is_trusted_proxy(candidate, trusted_proxy_networks):
            return candidate
    return direct


def client_ip_for(request: Request) -> str:
    """当前请求的客户端 IP；配置缺失时按直连地址处理。"""
    runtime = get_runtime(request)
    config = getattr(runtime, "identity_config", None) or IdentityHttpConfig()
    return resolve_client_ip(request, config.trusted_proxy_networks)


def identity_http_config(request: Request) -> IdentityHttpConfig:
    runtime = get_runtime(request)
    return getattr(runtime, "identity_config", None) or IdentityHttpConfig()


def require_identity(request: Request) -> IdentityService:
    """取身份服务；未装配（缺密钥/MySQL/Redis 配置）时返回 503。"""
    runtime = get_runtime(request)
    service = getattr(runtime, "identity", None)
    if service is None:
        detail = getattr(runtime, "identity_error", None) or "身份服务未配置"
        raise HTTPException(status_code=503, detail=detail, headers=NO_STORE)
    return service


async def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
) -> Principal:
    """验证 Bearer 访问令牌并返回主体。

    无令牌先返回 401（与依赖是否可用无关）；带令牌而身份依赖故障时返回 503。
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="需要访问令牌",
            headers={**_UNAUTHORIZED_HEADERS, **NO_STORE},
        )

    service = require_identity(request)
    try:
        return await service.verify(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=401,
            detail="访问令牌无效或已失效",
            headers={**_UNAUTHORIZED_HEADERS, **NO_STORE},
        ) from exc
    except IdentityDependencyError as exc:
        raise HTTPException(status_code=503, detail="身份服务不可用", headers=NO_STORE) from exc


def configure_cors(app, settings) -> None:
    """为认证域配置 CORS：只允许明确配置的来源，绝不使用 `*`。

    `allow_credentials=True` 与通配来源互斥，这里通过只填入具体来源来保证。
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER_NAME],
    )


def install_validation_error_handler(app) -> None:
    """改写校验错误响应：不回显提交内容，且禁止缓存。

    pydantic 的 422 默认把 `input` 原样回显，提交超长或格式错误的密码时
    响应体里就会出现明文密码（还可能被上游日志留存）。这里只保留定位信息。
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        detail = [
            {
                "type": error.get("type"),
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": error.get("msg"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": detail}, headers=dict(NO_STORE)
        )


def configure_identity_http(app, settings) -> None:
    """装配认证域的 HTTP 行为：CORS 白名单与不回显提交内容的校验错误。"""
    configure_cors(app, settings)
    install_validation_error_handler(app)
