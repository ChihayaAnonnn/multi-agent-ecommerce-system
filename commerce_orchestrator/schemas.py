"""购物约束和可信候选 DTO。"""
from typing import Literal
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
