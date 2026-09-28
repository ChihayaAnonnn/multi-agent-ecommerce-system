"""Application-scoped services shared by HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from orchestrator.supervisor import SupervisorOrchestrator
from services.ab_test import ABTestEngine
from services.metrics import MetricsCollector


@dataclass
class ApiRuntime:
    ab_engine: ABTestEngine
    metrics_collector: MetricsCollector
    supervisor: SupervisorOrchestrator
    rec_graph: Any = None


def create_runtime() -> ApiRuntime:
    ab_engine = ABTestEngine()
    return ApiRuntime(
        ab_engine=ab_engine,
        metrics_collector=MetricsCollector(),
        supervisor=SupervisorOrchestrator(ab_engine=ab_engine),
    )


def get_runtime(request: Request) -> ApiRuntime:
    return request.app.state.runtime
