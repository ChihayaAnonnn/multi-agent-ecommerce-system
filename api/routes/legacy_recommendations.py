"""Recommendation HTTP endpoints."""

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import ApiRuntime, get_runtime
from legacy.models.schemas import RecommendationRequest, RecommendationResponse

router = APIRouter()


@router.post("/api/v1/recommend", response_model=RecommendationResponse)
async def recommend(
    request: RecommendationRequest,
    runtime: ApiRuntime = Depends(get_runtime),
) -> RecommendationResponse:
    """使用 Supervisor 编排器进行推荐。"""
    response = await runtime.supervisor.recommend(request)
    for name, result in response.agent_results.items():
        runtime.metrics_collector.record_agent_call(
            agent_name=name,
            success=result.success,
            latency_ms=result.latency_ms,
        )
    return response


@router.post("/api/v1/recommend/graph")
async def recommend_via_graph(
    request: RecommendationRequest,
    runtime: ApiRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """使用 LangGraph 状态图进行推荐。"""
    if not runtime.rec_graph:
        return {"error": "Graph not initialized"}
    state = {
        "user_id": request.user_id,
        "scene": request.scene,
        "num_items": request.num_items,
        "context": request.context,
    }
    result = await runtime.rec_graph.ainvoke(state)
    return {
        "request_id": result.get("request_id"),
        "user_id": result.get("user_id"),
        "products": [p.model_dump() for p in result.get("final_products", [])],
        "marketing_copies": result.get("marketing_copies", []),
        "experiment_group": result.get("experiment_group", "control"),
        "total_latency_ms": round(result.get("total_latency_ms", 0), 1),
    }
