"""应用使用的模型与单轮图端口。"""

from typing import Protocol, runtime_checkable

from ecom.application.shopping.state import CommerceGraphState
from ecom.kernel.intent import IntentUpdate, ShoppingIntent


@runtime_checkable
class IntentModel(Protocol):
    async def parse(self, message: str, current: ShoppingIntent) -> IntentUpdate: ...


@runtime_checkable
class CandidateReranker(Protocol):
    async def rank(
        self, candidate_ids: list[str], facts: dict, soft_preferences: list[str],
    ) -> list[str]: ...


class CommerceGraph(Protocol):
    async def ainvoke(self, state: CommerceGraphState) -> CommerceGraphState: ...
