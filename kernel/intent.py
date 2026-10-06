"""购物意图及来源约束；不依赖 HTTP 或持久化模块。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ['Source', 'HardCondition', 'ShoppingIntent', 'IntentUpdate']

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
