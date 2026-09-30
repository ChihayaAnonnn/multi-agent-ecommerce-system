"""Application-scoped services shared by HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from checkout.service import CheckoutService
from checkout.gateway import DemoHostCheckoutGateway, UnavailableHostCheckoutGateway
from identity.types import DEMO_ENVIRONMENTS
from cart.service import CartService
from catalog.service import CatalogService
from shopping_session.service import ShoppingSessionService
from commerce_orchestrator.facts import CommerceFactsReader
from commerce_orchestrator.service import CommerceOrchestrator
from commerce_orchestrator.model import LLMCommerceModel, SafeIntentModel, SafeCandidateReranker
from config import get_settings
from db.session import create_session_factory
from identity.csrf import CsrfSigner
from identity.rate_limit import RedisRateLimiter
from identity.service import IdentityService
from identity.tokens import load_token_codec
from identity.types import (
    IdentityConfigurationError,
    IdentityHttpConfig,
    parse_trusted_networks,
)
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
    identity: IdentityService | None = None
    identity_config: IdentityHttpConfig | None = None
    # 身份未装配时的原因；认证与受保护端点据此返回 503。
    identity_error: str | None = None
    checkout: CheckoutService | None = None
    cart: CartService | None = None
    commerce: CommerceOrchestrator | None = None
    shopping_sessions: ShoppingSessionService | None = None
    redis: Redis | None = None

    async def aclose(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None


def build_identity_service(settings, session_factory, redis_client) -> IdentityService:
    """按配置装配身份服务；任何配置缺口都抛 `IdentityConfigurationError`。"""
    codec = load_token_codec(settings)
    csrf = CsrfSigner(settings.identity_csrf_secret)
    limiter = RedisRateLimiter(
        redis_client,
        login_email_limit=settings.identity_login_email_limit,
        login_ip_limit=settings.identity_login_ip_limit,
        register_ip_limit=settings.identity_register_ip_limit,
        refresh_session_limit=settings.identity_refresh_session_limit,
        login_window_seconds=settings.identity_login_window_seconds,
        register_window_seconds=settings.identity_register_window_seconds,
        refresh_window_seconds=settings.identity_refresh_window_seconds,
    )
    return IdentityService(
        session_factory,
        codec,
        limiter,
        csrf,
        settings.identity_environment,
        settings.identity_demo_mode,
        session_ttl_seconds=settings.identity_session_ttl_seconds,
    )


def create_runtime() -> ApiRuntime:
    """构造运行时。

    数据库未配置或不是 MySQL URL 时，不初始化目录/价格/库存服务：
    旧推荐接口必须照常启动，新只读端点返回明确的 503。
    身份配置缺失时只让认证与受保护端点 503，不影响旧推荐与公开只读接口。
    """
    settings = get_settings()
    ab_engine = ABTestEngine()
    runtime = ApiRuntime(
        ab_engine=ab_engine,
        metrics_collector=MetricsCollector(),
        supervisor=SupervisorOrchestrator(ab_engine=ab_engine),
        identity_config=IdentityHttpConfig(
            allowed_origins=settings.allowed_origins,
            trusted_proxy_networks=settings.trusted_proxy_networks,
            session_ttl_seconds=settings.identity_session_ttl_seconds,
        ),
    )

    try:
        # 可信代理网段配错时同样视为配置故障，不静默忽略
        parse_trusted_networks(settings.trusted_proxy_networks)
        if "*" in settings.identity_allowed_origins:
            raise IdentityConfigurationError("认证域 CORS 不允许使用通配来源 *")
    except IdentityConfigurationError as exc:
        runtime.identity_error = str(exc)
        return runtime

    try:
        session_factory = create_session_factory(settings.database_url)
    except ValueError as exc:
        runtime.catalog_error = str(exc)
        runtime.identity_error = str(exc)
        return runtime

    runtime.shopping_sessions = ShoppingSessionService(session_factory, settings.commerce_session_ttl_seconds, settings.commerce_turn_lease_seconds)
    pricing = PricingService(session_factory)
    runtime.pricing = pricing
    runtime.inventory = InventoryService(session_factory)
    runtime.catalog = CatalogService(session_factory, price_reader=pricing)

    runtime.cart = CartService(session_factory, runtime.shopping_sessions, CommerceFactsReader(runtime.catalog, pricing, runtime.inventory), settings.commerce_max_line_quantity, settings.commerce_action_ttl_seconds)
    runtime.commerce = CommerceOrchestrator(runtime.shopping_sessions, CommerceFactsReader(runtime.catalog, pricing, runtime.inventory), runtime.catalog, settings.commerce_scan_limit)

    runtime.commerce.cart = runtime.cart
    gateway = DemoHostCheckoutGateway(settings.identity_environment) if settings.commerce_demo_checkout_enabled and settings.identity_environment in DEMO_ENVIRONMENTS else UnavailableHostCheckoutGateway()
    runtime.checkout = CheckoutService(session_factory, runtime.shopping_sessions, runtime.cart, gateway,
        tuple(h.strip() for h in settings.commerce_checkout_allowed_hosts.split(',') if h.strip()), settings.commerce_checkout_allowed_path)
    runtime.commerce.checkout = runtime.checkout
    if settings.llm_api_key:
        model = LLMCommerceModel(settings)
        runtime.commerce.intent_model = SafeIntentModel(model)
        runtime.commerce.reranker = SafeCandidateReranker(model)
    runtime.commerce.configure_graph()

    try:
        runtime.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        runtime.identity = build_identity_service(settings, session_factory, runtime.redis)
    except IdentityConfigurationError as exc:
        runtime.identity_error = str(exc)
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
