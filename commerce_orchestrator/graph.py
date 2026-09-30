"""独立、单轮、无 checkpointer 的 commerce StateGraph。"""
from dataclasses import dataclass,field
from typing import Any
from langgraph.graph import StateGraph,END
from commerce_orchestrator.schemas import CommerceGraphState
from commerce_orchestrator.model import SafeIntentModel,SafeCandidateReranker
from commerce_orchestrator.nodes import CommerceNodes


@dataclass
class CommerceGraphDependencies:
    discovery: Any
    facts: Any
    comparison: Any
    catalog: Any
    sessions: Any
    cart: Any=None
    checkout: Any=None
    intent_model: SafeIntentModel=field(default_factory=SafeIntentModel)
    reranker: SafeCandidateReranker=field(default_factory=SafeCandidateReranker)


def build_commerce_graph(deps: CommerceGraphDependencies):
    graph=StateGraph(CommerceGraphState)
    nodes=CommerceNodes(deps)
    names=['parse_intent','validate_route','discover','quote_filter','rank_explain','compare','read_cart','propose_cart_action','preview_checkout','clarify','unsupported','unavailable','finalize']
    for name in names:
        graph.add_node(name,nodes.wrapped(name))
    graph.set_entry_point('parse_intent')
    graph.add_edge('parse_intent','validate_route')
    graph.add_conditional_edges('validate_route',lambda s:s['route'],{
        'shop':'discover','selection':'discover','compare':'compare','cart_read':'read_cart',
        'cart_mutation':'propose_cart_action','checkout':'preview_checkout','clarify':'clarify',
        'unsupported':'unsupported','unavailable':'unavailable'})
    graph.add_conditional_edges('discover',lambda s:'error' if s['route'] in {'clarify','unavailable'} else 'next',{'error':'finalize','next':'quote_filter'})
    graph.add_conditional_edges('quote_filter',lambda s:'error' if s['route'] in {'clarify','unavailable'} else 'next',{'error':'finalize','next':'rank_explain'})
    for name in ['rank_explain','compare','read_cart','propose_cart_action','preview_checkout','clarify','unsupported','unavailable']:
        graph.add_edge(name,'finalize')
    graph.add_edge('finalize',END)
    return graph.compile(name='commerce')
