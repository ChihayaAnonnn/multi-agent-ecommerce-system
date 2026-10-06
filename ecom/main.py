"""组合根：`create_app()` 工厂、lifespan 与路由注册。

应用对象只在 `create_app()` 中构造，运行时（Runtime）只在 lifespan 中创建：
`import ecom.main` 不建连接、不构造应用与 Runtime。旧推荐栈在导入期仍会构造
`legacy/orchestrator/graph.py` 的 Agent 单例，这是搬迁前的既有行为，本任务不改变。

配置只有一个来源：工厂解析出的 `Settings` 写入 `app.state.settings`，lifespan 与
路由都从这里取，不再回退全局 `get_settings()`，否则注入配置与全局配置会分裂。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from ecom.api.deps import create_runtime
from ecom.api.http_config import IdentityHttpConfig, parse_http_list
from ecom.api.middleware import configure_identity_http
from ecom.api.routes.auth import router as auth_router
from ecom.api.routes.catalog import quote_router
from ecom.api.routes.catalog import router as catalog_router
from ecom.api.routes.commerce import router as commerce_router
from ecom.api.routes.demo import configure_commerce_demo
from ecom.api.routes.health import router as health_router
from ecom.api.routes.legacy_experiments import router as experiments_router
from ecom.api.routes.legacy_recommendations import router as recommendations_router
from ecom.api.routes.metrics import router as metrics_router
from ecom.api.routes.storefront import router as storefront_router
from ecom.config import Settings, get_settings
from ecom.legacy.orchestrator.graph import build_recommendation_graph

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 与路由同源：只用在 `create_app()` 里解析并保存的这一份 Settings。
    settings: Settings = app.state.settings
    runtime = await create_runtime(settings=settings)
    try:
        runtime.rec_graph = build_recommendation_graph()
        app.state.runtime = runtime
        logger.info("app.startup", model=settings.llm_model)
        yield
    finally:
        await runtime.aclose()
        logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造已注册路由、中间件与 lifespan 的应用；此处不创建 Runtime。"""
    settings = settings if settings is not None else get_settings()
    app = FastAPI(
        title="Multi-Agent E-Commerce Recommendation System",
        # 折行只为行长；拼接后的 description 与搬迁前逐字一致（OpenAPI 契约）。
        description=(
            "用户画像Agent + 商品推荐Agent + 营销文案Agent + 库存决策Agent，"
            "并行+聚合模式"
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    # 应用实例保存唯一配置来源：lifespan 构造 Runtime、路由读取配置都用这一份。
    app.state.settings = settings

    # 认证域 CORS 只允许显式配置的前端来源，绝不使用通配符：
    # 带凭证的跨站请求不允许 `*`，未配置来源时按拒绝处理。
    configure_identity_http(app, IdentityHttpConfig(
        allowed_origins=parse_http_list(settings.identity_allowed_origins),
        trusted_proxy_networks=parse_http_list(settings.identity_trusted_proxy_networks),
        session_ttl_seconds=settings.identity_session_ttl_seconds,
    ))
    # 演示页是仓库级交付产物：位置由组合根按仓库根给出，路由模块不猜自身深度。
    configure_commerce_demo(
        app, settings, demo_dir=Path(__file__).resolve().parents[1] / "demo"
    )

    # 注册顺序决定 `app.routes` 顺序，属于冻结契约的一部分，不要调整。
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

    return app
