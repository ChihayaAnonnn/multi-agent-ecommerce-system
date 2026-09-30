"""FastAPI application setup and route registration."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from api.auth import router as auth_router
from api.catalog import router as catalog_router
from api.commerce_demo import configure_commerce_demo
from api.commerce import router as commerce_router
from api.commerce_read import router as commerce_read_router
from api.experiments import router as experiments_router
from api.health import router as health_router
from api.identity import configure_identity_http
from api.metrics import router as metrics_router
from api.recommendations import router as recommendations_router
from api.runtime import create_runtime
from config import get_settings
from orchestrator.graph import build_recommendation_graph

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.runtime.rec_graph = build_recommendation_graph()
    logger.info("app.startup", model=settings.llm_model)
    yield
    # 释放身份模块的 Redis 连接；未装配身份时是空操作
    await app.state.runtime.aclose()
    logger.info("app.shutdown")


app = FastAPI(
    title="Multi-Agent E-Commerce Recommendation System",
    description="用户画像Agent + 商品推荐Agent + 营销文案Agent + 库存决策Agent，并行+聚合模式",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.runtime = create_runtime()

# 认证域 CORS 只允许显式配置的前端来源，绝不使用通配符：
# 带凭证的跨站请求不允许 `*`，未配置来源时按拒绝处理。
configure_identity_http(app, settings)
configure_commerce_demo(app, settings)

for router in (
    health_router,
    recommendations_router,
    experiments_router,
    metrics_router,
    catalog_router,
    commerce_read_router,
    commerce_router,
    auth_router,
):
    app.include_router(router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
