"""模型超时与输出约束；失败时保守降级。"""

import asyncio

from application.shopping.ports import CandidateReranker, IntentModel
from kernel.intent import IntentUpdate, ShoppingIntent


class SafeIntentModel:
    def __init__(
        self, model: IntentModel | None = None, timeout_seconds: float = 5.0,
    ) -> None:
        self.model, self.timeout = model, timeout_seconds

    async def parse_result(
        self, message: str, current: ShoppingIntent,
    ) -> tuple[IntentUpdate, bool]:
        if self.model is None:
            return IntentUpdate(), True
        try:
            proposal = IntentUpdate.model_validate(await asyncio.wait_for(
                self.model.parse(message, current), self.timeout,
            ))
            data = {
                field: getattr(proposal, field)
                for field in proposal.model_fields_set & {'soft_preferences', 'purpose'}
            }
            data['sources'] = {field: 'inferred' for field in data}
            return IntentUpdate.model_validate(data), False
        except Exception:
            return IntentUpdate(), True

    async def parse(self, message: str, current: ShoppingIntent) -> IntentUpdate:
        return (await self.parse_result(message, current))[0]


class SafeCandidateReranker:
    def __init__(
        self, model: CandidateReranker | None = None, timeout_seconds: float = 5.0,
    ) -> None:
        self.model, self.timeout = model, timeout_seconds

    async def rank_result(
        self, candidate_ids: list[str], facts: dict, soft_preferences: list[str],
    ) -> tuple[list[str], bool]:
        ids = list(dict.fromkeys(candidate_ids))[:3]
        if self.model is None:
            return ids, True
        try:
            result = await asyncio.wait_for(self.model.rank(
                ids, {sku_id: facts[sku_id] for sku_id in ids if sku_id in facts},
                soft_preferences,
            ), self.timeout)
            if (
                not isinstance(result, list) or not result or len(result) > len(ids)
                or any(
                    not isinstance(sku_id, str) or sku_id not in ids
                    for sku_id in result
                )
                or len(set(result)) != len(result)
            ):
                return ids, True
            return result, False
        except Exception:
            return ids, True

    async def rank(
        self, candidate_ids: list[str], facts: dict, soft_preferences: list[str],
    ) -> list[str]:
        return (await self.rank_result(candidate_ids, facts, soft_preferences))[0]
