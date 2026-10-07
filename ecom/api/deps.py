"""Application-scoped services shared by HTTP routes."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ecom.api.http_config import (
    IdentityHttpConfig,
    parse_http_list,
    parse_trusted_networks,
)
from ecom.application.shopping.actions import CartActionService
from ecom.application.shopping.compare import CompareService
from ecom.application.shopping.discovery import DiscoveryService
from ecom.application.shopping.facts import CommerceFactsReader
from ecom.application.shopping.graph import (
    CommerceGraphDependencies,
    build_commerce_graph,
)
from ecom.application.shopping.guards import SafeCandidateReranker, SafeIntentModel
from ecom.application.shopping.orchestrator import CommerceOrchestrator
from ecom.application.shopping.ports import CandidateReranker, IntentModel
from ecom.config import Settings, get_settings
from ecom.infra.checkout_gateway import (
    DemoHostCheckoutGateway,
    UnavailableHostCheckoutGateway,
)
from ecom.infra.db import DatabaseConfigurationError, create_database
from ecom.infra.llm import LLMCommerceModel
from ecom.infra.redis import create_redis
from ecom.legacy.orchestrator.supervisor import SupervisorOrchestrator
from ecom.legacy.services.ab_test import ABTestEngine
from ecom.legacy.services.metrics import MetricsCollector
from ecom.modules.cart.service import CartService
from ecom.modules.catalog.service import CatalogService, CatalogSKUReader
from ecom.modules.checkout.ports import HostCheckoutGateway
from ecom.modules.checkout.service import CheckoutService
from ecom.modules.events.service import CommerceEventService
from ecom.modules.identity.csrf import CsrfSigner
from ecom.modules.identity.rate_limit import RedisRateLimiter
from ecom.modules.identity.service import IdentityService
from ecom.modules.identity.tokens import load_token_codec
from ecom.modules.identity.types import (
    DEMO_ENVIRONMENTS,
    IdentityConfigurationError,
)
from ecom.modules.inventory.service import InventoryService
from ecom.modules.pricing.service import PricingService
from ecom.modules.sale_facts.service import SaleFactsReader
from ecom.modules.sessions.service import ShoppingSessionService


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
    commerce_events: CommerceEventService | None = None
    checkout: CheckoutService | None = None
    cart: CartService | None = None
    cart_actions: CartActionService | None = None
    commerce: CommerceOrchestrator | None = None
    shopping_sessions: ShoppingSessionService | None = None
    redis: Redis | None = None
    owned_engine: AsyncEngine | None = None
    owned_redis: Any = None
    _resources: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)

    async def aclose(self) -> None:
        await self._resources.aclose()


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


async def create_runtime(
    settings: Settings | None = None, *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Any = None,
    gateway: HostCheckoutGateway | None = None,
    model: IntentModel | None = None,
) -> ApiRuntime:
    """构造运行时。

    数据库未配置或不是 MySQL URL 时，不初始化目录/价格/库存服务：
    旧推荐接口必须照常启动，新只读端点返回明确的 503。
    身份配置缺失时只让认证与受保护端点 503，不影响旧推荐与公开只读接口。
    """
    settings = settings if settings is not None else get_settings()
    ab_engine = ABTestEngine()
    base = dict(
        ab_engine=ab_engine,
        metrics_collector=MetricsCollector(),
        supervisor=SupervisorOrchestrator(ab_engine=ab_engine),
        identity_config=IdentityHttpConfig(
            allowed_origins=parse_http_list(settings.identity_allowed_origins),
            trusted_proxy_networks=parse_http_list(settings.identity_trusted_proxy_networks),
            session_ttl_seconds=settings.identity_session_ttl_seconds,
        ),
    )

    try:
        parse_trusted_networks(base['identity_config'].trusted_proxy_networks)
        if "*" in settings.identity_allowed_origins:
            raise IdentityConfigurationError("认证域 CORS 不允许使用通配来源 *")
    except IdentityConfigurationError as exc:
        return ApiRuntime(**base, identity_error=str(exc))

    resources = AsyncExitStack()
    async with AsyncExitStack() as construction_guard:
        construction_guard.push_async_callback(resources.aclose)
        owned_engine = owned_redis = None
        if session_factory is None:
            try:
                database = create_database(settings.database_url)
            except DatabaseConfigurationError as exc:
                return ApiRuntime(
                    **base, catalog_error=str(exc), identity_error=str(exc),
                )
            owned_engine = database.engine
            resources.push_async_callback(owned_engine.dispose)
            session_factory = database.session_factory

        sku_reader = CatalogSKUReader(session_factory)
        pricing = PricingService(session_factory, sku_reader)
        inventory = InventoryService(session_factory, sku_reader)
        catalog = CatalogService(session_factory, price_reader=pricing)
        facts = SaleFactsReader(catalog, pricing, inventory)
        sessions = ShoppingSessionService(
            session_factory, settings.commerce_session_ttl_seconds,
            settings.commerce_turn_lease_seconds,
        )
        cart = CartService(
            session_factory, facts, settings.commerce_max_line_quantity,
            settings.commerce_action_ttl_seconds,
        )
        if gateway is None:
            gateway = (
                DemoHostCheckoutGateway(settings.identity_environment)
                if settings.commerce_demo_checkout_enabled
                and settings.identity_environment in DEMO_ENVIRONMENTS
                else UnavailableHostCheckoutGateway()
            )
        checkout = CheckoutService(
            session_factory, cart, gateway,
            tuple(
                host.strip()
                for host in settings.commerce_checkout_allowed_hosts.split(',')
                if host.strip()
            ),
            settings.commerce_checkout_allowed_path,
        )
        events = CommerceEventService(session_factory, sessions)
        actions = CartActionService(session_factory, sessions, cart, checkout)
        commerce_facts = CommerceFactsReader(catalog, facts)
        discovery = DiscoveryService(
            commerce_facts, catalog, settings.commerce_scan_limit,
        )
        comparison = CompareService(sessions, catalog, commerce_facts)
        if model is None and settings.llm_api_key:
            model = LLMCommerceModel(settings)
        if model is not None and (
            not isinstance(model, IntentModel)
            or not isinstance(model, CandidateReranker)
        ):
            raise TypeError('注入模型必须同时实现 IntentModel 与 CandidateReranker')
        graph = build_commerce_graph(CommerceGraphDependencies(
            discovery=discovery, facts=commerce_facts, comparison=comparison,
            catalog=catalog, sessions=sessions, cart=cart, actions=actions,
            intent_model=SafeIntentModel(model), reranker=SafeCandidateReranker(model),
        ))
        commerce = CommerceOrchestrator(sessions, graph, actions, comparison)

        if redis_client is None:
            redis_client = create_redis(settings.redis_url)
            owned_redis = redis_client
            resources.push_async_callback(owned_redis.aclose)
        identity = None
        identity_error = None
        try:
            identity = build_identity_service(settings, session_factory, redis_client)
        except IdentityConfigurationError as exc:
            identity_error = str(exc)
        runtime = ApiRuntime(
            **base, catalog=catalog, pricing=pricing, inventory=inventory,
            shopping_sessions=sessions, cart=cart, checkout=checkout,
            commerce_events=events, cart_actions=actions, commerce=commerce,
            identity=identity, identity_error=identity_error, redis=redis_client,
            owned_engine=owned_engine, owned_redis=owned_redis, _resources=resources,
        )
        construction_guard.pop_all()
        return runtime


def get_runtime(request: Request) -> ApiRuntime:
    return request.app.state.runtime


def get_app_settings(request: Request) -> Settings:
    """当前应用实例的配置。

    只由组合根在 `create_app()` 写入 `app.state.settings`；路由必须读这一份，
    不得回退 `ecom.config.get_settings()`，否则注入配置与全局配置会分裂。
    """
    return request.app.state.settings


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
