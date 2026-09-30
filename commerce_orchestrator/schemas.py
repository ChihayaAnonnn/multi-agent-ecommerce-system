"""购物约束和可信候选 DTO。"""
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field
from catalog.schemas import SKUDetailView
from pricing.schemas import PriceView
from inventory.schemas import AvailabilityView
from db.base import UtcDateTime

Source = Literal['explicit', 'session', 'inferred']


class HardCondition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=64)
    value: str | int | float | bool
    source: Source = 'explicit'


class ShoppingIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category_id: str | None = None
    query_text: str | None = Field(default=None, max_length=255)
    budget_max_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: Literal['CNY'] = 'CNY'
    purpose: str | None = None
    hard_conditions: list[HardCondition] = Field(default_factory=list, max_length=20)
    soft_preferences: list[str] = Field(default_factory=list, max_length=20)
    sources: dict[str, Source] = Field(default_factory=dict)

    def apply(self, update: 'IntentUpdate') -> 'ShoppingIntent':
        data = self.model_dump()
        for field in update.model_fields_set - {'sources'}:
            source = update.sources.get(field, 'explicit')
            if source == 'inferred' and field not in {'soft_preferences', 'purpose'}:
                continue
            data[field] = getattr(update, field)
            data['sources'][field] = source
        return ShoppingIntent.model_validate(data)


class IntentUpdate(ShoppingIntent):
    """仅 fields_set 中的已验证字段参与覆盖。"""
    pass


class SKUQuote(BaseModel):
    sku_id: str
    sku: SKUDetailView | None = None
    price: PriceView | None = None
    availability: AvailabilityView | None = None
    purchasable: bool
    reason: str
    read_at: UtcDateTime


class CandidatePage(BaseModel):
    items: list[SKUQuote] = Field(default_factory=list)
    incomplete: bool = False
    scanned: int = 0

# 仅单次 ainvoke 有效，MySQL 才是跨轮状态的事实来源。
from typing import TypedDict, TYPE_CHECKING
from identity.types import Principal
if TYPE_CHECKING:
    from shopping_session.schemas import TurnClaim
    from models.commerce import TurnRequest,TurnResponse
else:
    # LangGraph 会运行 get_type_hints；边界已由领域 DTO 校验，避免反向导入循环。
    TurnClaim = TurnRequest = TurnResponse = Any


class CommerceGraphState(TypedDict,total=False):
    principal: 'Principal'
    claim: 'TurnClaim'
    turn: 'TurnRequest'
    current: ShoppingIntent
    route: str
    candidate_ids: list[str]
    facts: dict
    candidates: list[dict]
    response: 'TurnResponse'
    trace: list[str]
    model_degraded: bool
