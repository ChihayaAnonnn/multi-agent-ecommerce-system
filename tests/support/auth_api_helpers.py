"""认证与购物 HTTP 验收共用的应用装配、运行时替身与请求助手。

来自 `tests/integration/test_auth_api.py`，供 auth/commerce/demo/e2e 多个测试
模块复用；测试模块不再兼任共享装配库。
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from ecom.api.deps import build_identity_service
from ecom.api.http_config import IdentityHttpConfig, parse_http_list
from ecom.api.middleware import configure_identity_http
from ecom.api.routes.auth import require_principal
from ecom.api.routes.auth import router as auth_router
from ecom.api.routes.catalog import quote_router
from ecom.api.routes.catalog import router as catalog_router
from ecom.api.routes.health import router as health_router
from ecom.api.routes.legacy_recommendations import router as recommendations_router
from ecom.config import Settings
from ecom.infra.db import create_session_factory
from ecom.legacy.models.schemas import Product, RecommendationResponse
from ecom.legacy.services.ab_test import ABTestEngine
from ecom.legacy.services.metrics import MetricsCollector
from ecom.modules.catalog.service import CatalogService, CatalogSKUReader
from ecom.modules.identity.types import Principal
from ecom.modules.inventory.service import InventoryService
from ecom.modules.pricing.service import PricingService
from tests.support.identity_helpers import DEMO_PASSWORD

BASE_URL = "https://test"


class StubSupervisor:
    """旧推荐接口的替身：只验证契约不变，不触发 LLM。"""

    async def recommend(self, request):
        return RecommendationResponse(
            request_id="req-auth",
            user_id=request.user_id,
            products=[Product(
                product_id="P001", name="商品", category="配件", price=99
            )],
            marketing_copies=[{"product_id": "P001", "copy": "商品说明"}],
            experiment_group="control",
            agent_results={},
            total_latency_ms=1.0,
        )


# ---------- 仅测试用的受保护路由 ----------

PROTECTED_RESOURCES: dict[str, str] = {}

protected_router = APIRouter()


@protected_router.get("/api/v1/test/owned-resources/{resource_id}")
async def read_owned_resource(
    resource_id: str, principal: Principal = Depends(require_principal)
):
    """按 `principal_key` 归属过滤：他人的资源一律 404。

    仅用于验证主体依赖，不代表购物会话/购物车授权已经实现。
    """
    if PROTECTED_RESOURCES.get(resource_id) != principal.principal_key:
        raise HTTPException(status_code=404, detail="资源不存在")
    return {"resource_id": resource_id, "subject": principal.subject}


@protected_router.get("/api/v1/test/client-ip")
async def echo_client_ip(request: Request):
    from ecom.api.middleware import client_ip_for

    return {"client_ip": client_ip_for(request)}


def build_app(runtime, settings: Settings) -> FastAPI:
    app = FastAPI()
    configure_identity_http(app, IdentityHttpConfig(
        allowed_origins=parse_http_list(settings.identity_allowed_origins),
        trusted_proxy_networks=parse_http_list(settings.identity_trusted_proxy_networks),
        session_ttl_seconds=settings.identity_session_ttl_seconds,
    ))
    for router in (
        health_router,
        recommendations_router,
        catalog_router,
        quote_router,
        auth_router,
        protected_router,
    ):
        app.include_router(router)
    app.state.settings = settings
    app.state.runtime = runtime
    return app


def build_runtime(
    database_url: str, redis_url: str, settings: Settings, *, client
) -> SimpleNamespace:
    """在事件循环内装配运行时；Redis 连接属于当前循环。"""
    factory = create_session_factory(database_url)
    sku_reader = CatalogSKUReader(factory)
    pricing = PricingService(factory, sku_reader)
    redis_client = client or Redis.from_url(redis_url, decode_responses=True)
    return SimpleNamespace(
        ab_engine=ABTestEngine(),
        metrics_collector=MetricsCollector(),
        supervisor=StubSupervisor(),
        rec_graph=None,
        catalog=CatalogService(factory, price_reader=pricing),
        pricing=pricing,
        inventory=InventoryService(factory, sku_reader),
        catalog_error=None,
        identity=build_identity_service(settings, factory, redis_client),
        identity_config=IdentityHttpConfig(
            allowed_origins=parse_http_list(settings.identity_allowed_origins),
            trusted_proxy_networks=parse_http_list(
                settings.identity_trusted_proxy_networks
            ),
            session_ttl_seconds=settings.identity_session_ttl_seconds,
        ),
        identity_error=None,
        redis=redis_client,
    )


def http_client(app, *, client=("198.51.100.7", 1234)) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=client), base_url=BASE_URL
    )  # noqa: E501


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register(client: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def login(client: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
