import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.legacy.models.schemas import AgentResult, Product, RecommendationResponse
from ecom.legacy.services.ab_test import ABTestEngine
from ecom.legacy.services.metrics import MetricsCollector


class StubSupervisor:
    async def recommend(self, request):
        return RecommendationResponse(
            request_id="req-123",
            user_id=request.user_id,
            products=[Product(product_id="P001", name="商品", category="配件", price=99)],
            marketing_copies=[{"product_id": "P001", "copy": "商品说明"}],
            experiment_group="control",
            agent_results={
                "inventory": AgentResult(agent_name="inventory", success=True, latency_ms=12)
            },
            total_latency_ms=25,
        )


class StubGraph:
    async def ainvoke(self, state):
        return {
            "request_id": "graph-123",
            "user_id": state["user_id"],
            "final_products": [Product(product_id="P002", name="图商品", category="配件", price=59)],
            "marketing_copies": [],
            "experiment_group": "control",
            "total_latency_ms": 12.34,
        }


def build_test_app():
    from ecom.api.routes.catalog import quote_router
    from ecom.api.routes.catalog import router as catalog_router
    from ecom.api.routes.health import router as health_router
    from ecom.api.routes.legacy_experiments import router as experiments_router
    from ecom.api.routes.legacy_recommendations import router as recommendations_router
    from ecom.api.routes.metrics import router as metrics_router
    from ecom.config import Settings

    app = FastAPI()
    for router in (
        health_router,
        recommendations_router,
        experiments_router,
        metrics_router,
        catalog_router,
        quote_router,
    ):
        app.include_router(router)
    # 与组合根一致：/health 只读应用实例上的配置，不再回退全局 get_settings()。
    app.state.settings = Settings(_env_file=None, llm_model="test-model")
    app.state.runtime = SimpleNamespace(
        ab_engine=ABTestEngine(),
        metrics_collector=MetricsCollector(),
        supervisor=StubSupervisor(),
        rec_graph=StubGraph(),
    )
    return app


def request(app, method, path, **kwargs):
    async def send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_recommendation_route_returns_product_and_records_agent_metric():
    app = build_test_app()
    response = request(app, "POST", "/api/v1/recommend", json={"user_id": "u-1", "num_items": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-123"
    assert body["products"][0]["product_id"] == "P001"
    assert body["marketing_copies"] == [{"product_id": "P001", "copy": "商品说明"}]
    assert body["agent_results"]["inventory"]["success"] is True
    metrics = request(app, "GET", "/api/v1/metrics")
    assert metrics.json()["agents"]["inventory"]["call_count"] == 1


def test_graph_route_keeps_its_response_shape():
    app = build_test_app()
    response = request(app, "POST", "/api/v1/recommend/graph", json={"user_id": "u-2"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "graph-123"
    assert body["products"][0]["product_id"] == "P002"
    assert body["total_latency_ms"] == 12.3
    assert "agent_results" not in body


def test_experiment_outcome_and_health_routes_keep_contract():
    app = build_test_app()
    outcome = request(
        app,
        "POST",
        "/api/v1/experiments/rec_strategy/outcome",
        params={"group": "control", "success": "true"},
    )
    assert outcome.status_code == 200
    assert outcome.json() == {"status": "recorded"}

    experiments = request(app, "GET", "/api/v1/experiments")
    assert experiments.status_code == 200
    control = experiments.json()["rec_strategy"]["groups"][0]
    assert control["name"] == "control"
    assert control["successes"] == 2

    health = request(app, "GET", "/health")
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "model": "test-model"}


def test_main_app_registers_all_existing_routes():
    from ecom.main import create_app

    app = create_app()
    paths = app.openapi()["paths"]
    routes = {(path, method.upper()) for path, methods in paths.items() for method in methods}
    assert {
        ("/health", "GET"),
        ("/api/v1/recommend", "POST"),
        ("/api/v1/recommend/graph", "POST"),
        ("/api/v1/experiments", "GET"),
        ("/api/v1/experiments/{experiment_id}/outcome", "POST"),
        ("/api/v1/metrics", "GET"),
        ("/api/v1/catalog/products", "GET"),
        ("/api/v1/catalog/products/{product_id}", "GET"),
        ("/api/v1/catalog/skus/{sku_id}", "GET"),
        ("/api/v1/commerce/skus/{sku_id}/quote", "GET"),
    } <= routes
