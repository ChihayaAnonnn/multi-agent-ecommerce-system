"""Application-scoped services shared by HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from catalog.service import CatalogService
from config import get_settings
from db.session import create_session_factory
from inventory.service import InventoryService
from orchestrator.supervisor import SupervisorOrchestrator
from pricing.service import PricingService
from services.ab_test import ABTestEngine
from services.metrics import MetricsCollector


@dataclass
class ApiRuntime:
    ab_engine: ABTestEngine
    metrics_collector: MetricsCollector
    supervisor: SupervisorOrchestrator
    rec_graph: Any = None
    catalog: CatalogService | None = None
    pricing: PricingService | None = None
    inventory: InventoryService | None = None
    # 未配置数据库时的原因，用于向调用方说明 503。
    catalog_error: str | None = None


def create_runtime() -> ApiRuntime:
    """构造运行时。

    数据库未配置或不是 MySQL URL 时，不初始化目录/价格/库存服务：
    旧推荐接口必须照常启动，新只读端点返回明确的 503。
    """
    ab_engine = ABTestEngine()
    runtime = ApiRuntime(
        ab_engine=ab_engine,
        metrics_collector=MetricsCollector(),
        supervisor=SupervisorOrchestrator(ab_engine=ab_engine),
    )

    try:
        session_factory = create_session_factory(get_settings().database_url)
    except ValueError as exc:
        runtime.catalog_error = str(exc)
        return runtime

    pricing = PricingService(session_factory)
    runtime.pricing = pricing
    runtime.inventory = InventoryService(session_factory)
    runtime.catalog = CatalogService(session_factory, price_reader=pricing)
    return runtime


def get_runtime(request: Request) -> ApiRuntime:
    return request.app.state.runtime


def _require(service: Any, runtime: ApiRuntime, name: str) -> Any:
    if service is None:
        detail = runtime.catalog_error or f"{name}服务未配置"
        raise HTTPException(status_code=503, detail=detail)
    return service


def require_catalog(request: Request) -> CatalogService:
    runtime = get_runtime(request)
    return _require(runtime.catalog, runtime, "目录")


def require_pricing(request: Request) -> PricingService:
    runtime = get_runtime(request)
    return _require(runtime.pricing, runtime, "价格")


def require_inventory(request: Request) -> InventoryService:
    runtime = get_runtime(request)
    return _require(runtime.inventory, runtime, "库存")
