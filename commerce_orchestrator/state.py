"""仅单次 ainvoke 有效；跨轮状态以 MySQL 为准。"""

from typing import TypedDict

from commerce_orchestrator.contracts import TurnRequest, TurnResponse
from kernel.intent import ShoppingIntent
from shared.principal import Principal
from shopping_session.schemas import TurnClaim

__all__ = ['CommerceGraphState']


class CommerceGraphState(TypedDict, total=False):
    principal: Principal
    claim: TurnClaim
    turn: TurnRequest
    current: ShoppingIntent
    route: str
    candidate_ids: list[str]
    facts: dict
    candidates: list[dict]
    response: TurnResponse
    trace: list[str]
    model_degraded: bool
