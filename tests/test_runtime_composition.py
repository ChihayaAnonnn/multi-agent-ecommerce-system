"""完整装配、首轮图复用与资源所有权的本地回归。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api import runtime as runtime_module
from checkout.schemas import Destination
from config.settings import Settings
from kernel.intent import IntentUpdate
from shared.principal import make_principal
from shopping_session.schemas import TurnClaim


class LocalModel:
    async def parse(self, message, current):
        return IntentUpdate()

    async def rank(self, candidate_ids, facts, soft_preferences):
        return candidate_ids


class ResourceSpy:
    def __init__(self, name, closed):
        self.name = name
        self.closed = closed
        self.calls = 0

    async def aclose(self):
        self.calls += 1
        self.closed.append(self.name)

    async def dispose(self):
        await self.aclose()


@pytest.fixture
def runtime_case(monkeypatch):
    closed = []
    engine = ResourceSpy('engine', closed)
    redis = ResourceSpy('redis', closed)
    factory = object()
    settings = Settings(
        _env_file=None, database_url='mysql+asyncmy://synthetic', llm_api_key='',
        identity_allowed_origins='', identity_trusted_proxy_networks='',
    )
    monkeypatch.setattr(runtime_module, 'create_database', lambda url: SimpleNamespace(
        engine=engine, session_factory=factory,
    ), raising=False)
    monkeypatch.setattr(runtime_module.Redis, 'from_url', lambda *args, **kwargs: redis)
    monkeypatch.setattr(
        runtime_module, 'build_identity_service', lambda *args: object(),
    )
    return SimpleNamespace(
        settings=settings, engine=engine, redis=redis, factory=factory,
        closed=closed, model=LocalModel(),
    )


def test_runtime_uses_one_actions_cart_checkout_and_model(runtime_case, monkeypatch):
    from commerce_orchestrator.graph import build_commerce_graph

    captured = []

    def build(deps):
        captured.append(deps)
        return build_commerce_graph(deps)

    monkeypatch.setattr(runtime_module, 'build_commerce_graph', build, raising=False)

    async def scenario():
        gateway = SimpleNamespace(available=False)
        runtime = await runtime_module.create_runtime(
            runtime_case.settings, session_factory=runtime_case.factory,
            redis_client=runtime_case.redis, gateway=gateway, model=runtime_case.model,
        )
        deps, = captured
        assert deps.actions is runtime.cart_actions is runtime.commerce.actions
        assert deps.cart is runtime.cart is runtime.cart_actions.cart
        assert runtime.cart_actions.checkout is runtime.checkout
        assert runtime.checkout.cart is runtime.cart
        assert runtime.checkout.gateway is gateway
        assert deps.intent_model.model is deps.reranker.model is runtime_case.model
        assert deps.comparison is runtime.commerce.comparison
        assert deps.sessions is runtime.shopping_sessions is runtime.commerce.sessions
        assert deps.discovery.facts is deps.facts
        assert deps.facts.facts is runtime.cart.facts
        assert deps.catalog is runtime.catalog is runtime.cart.facts.catalog
        assert runtime.pricing._sku_reader is runtime.inventory._sku_reader
        assert runtime.catalog._price_reader is runtime.pricing
        assert runtime.cart.facts.inventory is runtime.inventory
        assert runtime.commerce_events.sessions is runtime.shopping_sessions
        await runtime.aclose()

    asyncio.run(scenario())


def test_graph_compiles_once_before_first_request(runtime_case, monkeypatch):
    from langgraph.graph import StateGraph

    from commerce_orchestrator.contracts import TurnRequest

    compiled = []
    original = StateGraph.compile

    def compile_graph(graph, *args, **kwargs):
        result = original(graph, *args, **kwargs)
        compiled.append(result)
        return result

    monkeypatch.setattr(StateGraph, 'compile', compile_graph)

    async def scenario():
        runtime = await runtime_module.create_runtime(runtime_case.settings)
        assert compiled == [runtime.commerce.graph]
        principal = make_principal('test', 'runtime')
        completions = []

        async def claim(session_id, principal, turn_id, digest):
            return TurnClaim(
                session_id=session_id, principal_key=principal.principal_key,
                turn_id=turn_id, lease_token='lease', session_version=1,
            )

        async def complete(claim, completion):
            completions.append(completion)

        monkeypatch.setattr(runtime.shopping_sessions, 'claim_turn', claim)
        monkeypatch.setattr(runtime.shopping_sessions, 'complete_turn', complete)
        for turn_id in ['first', 'second']:
            response = await runtime.commerce.turn(
                principal, 'session', TurnRequest(turn_id=turn_id, message='查订单'),
            )
            assert response.status == 'unsupported'
        assert len(completions) == 2
        assert compiled == [runtime.commerce.graph]
        await runtime.aclose()

    asyncio.run(scenario())


def test_owned_resources_close_once_on_shutdown(runtime_case):
    async def scenario():
        runtime = await runtime_module.create_runtime(runtime_case.settings)
        assert runtime.owned_engine is runtime_case.engine
        assert runtime.owned_redis is runtime_case.redis
        await runtime.aclose()
        await runtime.aclose()

    asyncio.run(scenario())
    assert runtime_case.closed == ['redis', 'engine']
    assert runtime_case.redis.calls == runtime_case.engine.calls == 1


@pytest.mark.parametrize('failure_point', ['graph', 'identity'])
def test_owned_resources_close_once_on_failure(
    runtime_case, monkeypatch, failure_point,
):
    def fail(*args):
        raise RuntimeError('construction failed')

    target = (
        'build_commerce_graph' if failure_point == 'graph' else 'build_identity_service'
    )
    monkeypatch.setattr(runtime_module, target, fail, raising=False)
    with pytest.raises(RuntimeError, match='construction failed'):
        asyncio.run(runtime_module.create_runtime(runtime_case.settings))
    assert runtime_case.engine.calls == 1
    if failure_point == 'identity':
        assert runtime_case.closed == ['redis', 'engine']
    else:
        assert runtime_case.closed == ['engine']


def test_injected_resources_are_not_closed(runtime_case, monkeypatch):
    async def scenario():
        runtime = await runtime_module.create_runtime(
            runtime_case.settings, session_factory=runtime_case.factory,
            redis_client=runtime_case.redis,
        )
        assert runtime.owned_engine is None and runtime.owned_redis is None
        await runtime.aclose()
        await runtime.aclose()

    asyncio.run(scenario())
    assert runtime_case.closed == []

    def fail(*args):
        raise RuntimeError('construction failed')

    monkeypatch.setattr(runtime_module, 'build_identity_service', fail)
    with pytest.raises(RuntimeError, match='construction failed'):
        asyncio.run(runtime_module.create_runtime(
            runtime_case.settings, session_factory=runtime_case.factory,
            redis_client=runtime_case.redis,
        ))
    assert runtime_case.closed == []


def test_injected_model_must_also_rerank(runtime_case):
    with pytest.raises(TypeError, match='CandidateReranker'):
        asyncio.run(runtime_module.create_runtime(
            runtime_case.settings,
            model=SimpleNamespace(parse=runtime_case.model.parse),
        ))


def test_injected_model_must_parse_intent(runtime_case):
    with pytest.raises(TypeError, match='IntentModel'):
        asyncio.run(runtime_module.create_runtime(
            runtime_case.settings,
            model=SimpleNamespace(rank=runtime_case.model.rank),
        ))


def test_unexpected_database_value_error_is_not_configuration_failure(
    runtime_case, monkeypatch,
):
    def fail(url):
        raise ValueError('unexpected database construction failure')

    monkeypatch.setattr(runtime_module, 'create_database', fail)
    with pytest.raises(ValueError, match='unexpected database construction failure'):
        asyncio.run(runtime_module.create_runtime(runtime_case.settings))


def test_final_runtime_construction_failure_closes_owned_resources(
    runtime_case, monkeypatch,
):
    def fail(**kwargs):
        raise RuntimeError('runtime construction failed')

    monkeypatch.setattr(runtime_module, 'ApiRuntime', fail)
    with pytest.raises(RuntimeError, match='runtime construction failed'):
        asyncio.run(runtime_module.create_runtime(runtime_case.settings))
    assert runtime_case.closed == ['redis', 'engine']


def test_close_failure_still_disposes_engine_once(runtime_case, monkeypatch):
    async def fail():
        await runtime_case.redis.aclose()
        raise RuntimeError('redis close failed')

    original_redis = runtime_case.redis
    monkeypatch.setattr(runtime_module.Redis, 'from_url', lambda *args, **kwargs:
        SimpleNamespace(aclose=fail))

    async def scenario():
        runtime = await runtime_module.create_runtime(runtime_case.settings)
        with pytest.raises(RuntimeError, match='redis close failed'):
            await runtime.aclose()
        await runtime.aclose()

    asyncio.run(scenario())
    assert original_redis.calls == runtime_case.engine.calls == 1
    assert runtime_case.closed == ['redis', 'engine']


def test_missing_database_preserves_health_and_503():
    from api.auth import router as auth_router
    from api.catalog import router as catalog_router
    from api.health import router as health_router

    async def scenario():
        runtime = await runtime_module.create_runtime(Settings(
            _env_file=None, database_url='', llm_api_key='',
            identity_allowed_origins='', identity_trusted_proxy_networks='',
        ))
        app = FastAPI()
        app.state.runtime = runtime
        for router in [health_router, catalog_router, auth_router]:
            app.include_router(router)
        async with AsyncClient(
            transport=ASGITransport(app), base_url='https://test',
        ) as client:
            assert (await client.get('/health')).status_code == 200
            response = await client.get('/api/v1/catalog/products')
            assert response.status_code == 503
            assert response.json() == {'detail': runtime.catalog_error}
            assert runtime.catalog_error == runtime.identity_error == (
                '数据库未配置：请将 ECOM_DATABASE_URL 设置为 mysql+asyncmy:// URL'
            )
        await runtime.aclose()

    asyncio.run(scenario())


def test_known_identity_configuration_error_keeps_commerce(runtime_case, monkeypatch):
    from identity.types import IdentityConfigurationError

    def fail(*args):
        raise IdentityConfigurationError('missing identity keys')

    monkeypatch.setattr(runtime_module, 'build_identity_service', fail)

    async def scenario():
        runtime = await runtime_module.create_runtime(runtime_case.settings)
        assert runtime.identity is None
        assert runtime.identity_error == 'missing identity keys'
        assert runtime.commerce.graph is not None
        assert runtime_case.closed == []
        await runtime.aclose()

    asyncio.run(scenario())
    assert runtime_case.closed == ['redis', 'engine']


@pytest.mark.parametrize('failure', ['body', 'legacy_graph'])
def test_lifespan_awaits_runtime_and_closes_on_failure(
    runtime_case, monkeypatch, failure,
):
    import main

    app = FastAPI()
    closed = []
    runtime = ResourceSpy('runtime', closed)

    async def create():
        assert not hasattr(app.state, 'runtime')
        await asyncio.sleep(0)
        return runtime

    def legacy_graph():
        if failure == 'legacy_graph':
            raise RuntimeError('legacy failed')
        return object()

    monkeypatch.setattr(main, 'create_runtime', create)
    monkeypatch.setattr(main, 'build_recommendation_graph', legacy_graph)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with main.lifespan(app):
                assert app.state.runtime is runtime
                raise RuntimeError('body failed')

    asyncio.run(scenario())
    assert closed == ['runtime']


def test_database_resources_expose_engine_and_legacy_factory(monkeypatch):
    from db import session

    assert hasattr(session, 'create_database')
    created = []
    engine = object()
    factory = object()
    monkeypatch.setattr(session, 'create_async_engine', lambda *args, **kwargs: engine)
    monkeypatch.setattr(session, 'async_sessionmaker', lambda *args, **kwargs: factory)
    resources = session.create_database('mysql+asyncmy://synthetic')
    assert resources.engine is engine
    assert resources.session_factory is factory

    def create(url):
        created.append(url)
        return resources

    monkeypatch.setattr(session, 'create_database', create)
    assert session.create_session_factory('synthetic') is factory
    assert created == ['synthetic']


@pytest.mark.parametrize(('kind', 'ref', 'demo', 'valid'), [
    ('internal', '/demo/commerce/handoffs/00000000-0000-0000-0000-000000000000',
     True, True),
    ('internal', '/demo/commerce/handoffs/00000000-0000-0000-0000-000000000000',
     False, False),
    ('internal', '//host/checkout/', True, False),
    ('https', 'https://shop.test/checkout/one', False, True),
    ('https', 'https://shop.test:443/checkout/one', False, True),
    ('https', 'http://shop.test/checkout/one', False, False),
    ('https', 'https://other.test/checkout/one', False, False),
    ('https', 'https://shop.test:444/checkout/one', False, False),
    ('https', 'https://user@shop.test/checkout/one', False, False),
    ('https', 'https://shop.test/checkout/one?query=yes', False, False),
    ('https', 'https://shop.test/checkout/one#fragment', False, False),
    ('https', 'https://shop.test/other/one', False, False),
    ('https', 'https://shop.test/checkout/%2e', False, False),
    ('https', 'https://shop.test/checkout/../one', False, False),
    ('https', 'https://shop.test/checkout/\\one', False, False),
])
def test_destination_validation_is_business_owned(kind, ref, demo, valid):
    from checkout import service

    assert service.validate_destination.__module__ == 'checkout.service'
    destination = Destination(kind=kind, ref=ref, demo=demo)
    if valid:
        assert service.validate_destination(destination, ('shop.test',)) is destination
    else:
        message = '非法内部交接路由' if kind == 'internal' else '非法宿主交接目的地'
        with pytest.raises(ValueError, match=message):
            service.validate_destination(destination, ('shop.test',))
