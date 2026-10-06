"""测试按显式依赖构造完整应用与单轮图。"""

from ecom.application.shopping.compare import CompareService
from ecom.application.shopping.discovery import DiscoveryService
from ecom.application.shopping.graph import (
    CommerceGraphDependencies,
    build_commerce_graph,
)
from ecom.application.shopping.guards import SafeCandidateReranker, SafeIntentModel
from ecom.application.shopping.orchestrator import CommerceOrchestrator


def build_orchestrator(sessions, facts, catalog, cart=None, actions=None):
    comparison = CompareService(sessions, catalog, facts)
    graph = build_commerce_graph(CommerceGraphDependencies(
        discovery=DiscoveryService(facts, catalog), facts=facts,
        comparison=comparison, catalog=catalog, sessions=sessions, cart=cart,
        actions=actions, intent_model=SafeIntentModel(),
        reranker=SafeCandidateReranker(),
    ))
    return CommerceOrchestrator(sessions, graph, actions, comparison)
