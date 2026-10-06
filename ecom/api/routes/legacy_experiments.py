"""Experiment status and outcome endpoints."""

from typing import Any

from fastapi import APIRouter, Depends

from ecom.api.deps import ApiRuntime, get_runtime

router = APIRouter()


@router.get("/api/v1/experiments")
async def get_experiments(runtime: ApiRuntime = Depends(get_runtime)) -> dict[str, Any]:
    experiments = {}
    for exp_id, exp in runtime.ab_engine.experiments.items():
        experiments[exp_id] = {
            "name": exp.name,
            "enabled": exp.enabled,
            "groups": [
                {
                    "name": group.name,
                    "weight": group.weight,
                    "config": group.config,
                    "successes": group.successes,
                    "failures": group.failures,
                }
                for group in exp.groups
            ],
            "stats": runtime.ab_engine.get_stats(exp_id),
        }
    return experiments


@router.post("/api/v1/experiments/{experiment_id}/outcome")
async def record_outcome(
    experiment_id: str,
    group: str,
    success: bool,
    runtime: ApiRuntime = Depends(get_runtime),
) -> dict[str, str]:
    runtime.ab_engine.record_outcome(experiment_id, group, success)
    return {"status": "recorded"}
