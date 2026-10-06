"""FastAPI application setup and route registration."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from api.deps import create_runtime
from api.http_config import IdentityHttpConfig, parse_http_list
from api.middleware import configure_identity_http
from api.routes.auth import router as auth_router
from api.routes.catalog import quote_router
from api.routes.catalog import router as catalog_router
from api.routes.commerce import router as commerce_router
from api.routes.demo import configure_commerce_demo
from api.routes.health import router as health_router
from api.routes.legacy_experiments import router as experiments_router
from api.routes.legacy_recommendations import router as recommendations_router
from api.routes.metrics import router as metrics_router
from api.routes.storefront import router as storefront_router
from config import get_settings
from legacy.orchestrator.graph import build_recommendation_graph

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    runtime = await create_runtime()
    try:
        runtime.rec_graph = build_recommendation_graph()
        app.state.runtime = runtime
        logger.info("app.startup", model=settings.llm_model)
        yield
    finally:
        await runtime.aclose()
        logger.info("app.shutdown")


app = FastAPI(
    title="Multi-Agent E-Commerce Recommendation System",
    description="用户画像Agent + 商品推荐Agent + 营销文案Agent + 库存决策Agent，并行+聚合模式",
    version="1.0.0",
    lifespan=lifespan,
)

# 认证域 CORS 只允许显式配置的前端来源，绝不使用通配符：
# 带凭证的跨站请求不允许 `*`，未配置来源时按拒绝处理。
configure_identity_http(app, IdentityHttpConfig(
    allowed_origins=parse_http_list(settings.identity_allowed_origins),
    trusted_proxy_networks=parse_http_list(settings.identity_trusted_proxy_networks),
    session_ttl_seconds=settings.identity_session_ttl_seconds,
))
# 演示页绝对位置由组合根给出：路由模块搬迁后不再从自身深度推断仓库根。
configure_commerce_demo(
    app, settings, demo_dir=Path(__file__).resolve().parent / "demo"
)

for router in (
    health_router,
    recommendations_router,
    experiments_router,
    metrics_router,
    catalog_router,
    storefront_router,
    quote_router,
    commerce_router,
    auth_router,
):
    app.include_router(router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
