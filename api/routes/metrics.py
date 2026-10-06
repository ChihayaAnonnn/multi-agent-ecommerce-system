"""Metrics endpoint."""

from typing import Any

from fastapi import APIRouter, Depends

from api.deps import ApiRuntime, get_runtime

router = APIRouter()


@router.get("/api/v1/metrics")
async def get_metrics(runtime: ApiRuntime = Depends(get_runtime)) -> dict[str, Any]:
    return {
        "agents": runtime.metrics_collector.get_agent_stats(),
        "business": runtime.metrics_collector.get_business_stats(),
    }
