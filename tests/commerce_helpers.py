"""测试按显式依赖构造完整应用与单轮图。"""

from commerce_orchestrator.compare import CompareService
from commerce_orchestrator.discovery import DiscoveryService
from commerce_orchestrator.graph import CommerceGraphDependencies, build_commerce_graph
from commerce_orchestrator.guards import SafeCandidateReranker, SafeIntentModel
from commerce_orchestrator.service import CommerceOrchestrator


def build_orchestrator(sessions, facts, catalog, cart=None, actions=None):
    comparison = CompareService(sessions, catalog, facts)
    graph = build_commerce_graph(CommerceGraphDependencies(
        discovery=DiscoveryService(facts, catalog), facts=facts,
        comparison=comparison, catalog=catalog, sessions=sessions, cart=cart,
        actions=actions, intent_model=SafeIntentModel(),
        reranker=SafeCandidateReranker(),
    ))
    return CommerceOrchestrator(sessions, graph, actions, comparison)
