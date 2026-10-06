"""单轮购物请求与响应契约；与 HTTP 输入和持久化解耦。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kernel.intent import ShoppingIntent

__all__ = ['TurnRequest', 'TurnResponse']


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: str = Field(min_length=1, max_length=64)
    message: str = Field(default='', max_length=2000)
    constraints: ShoppingIntent | None = None
    selected_product_id: str | None = None
    selected_sku_id: str | None = None
    product_ids: list[str] = Field(default_factory=list, max_length=3)
    quantity: int | None = Field(default=None, ge=1, strict=True)
    expected_cart_version: int | None = Field(default=None, ge=1, strict=True)
    respond_to_action_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=64)


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    session_id: str
    turn_id: str
    intent: str
    status: Literal[
        'clarify', 'shortlist', 'compare', 'confirm_action', 'cart_updated',
        'handoff', 'unsupported', 'unavailable',
    ]
    constraints: ShoppingIntent = Field(default_factory=ShoppingIntent)
    items: list[dict[str, Any]] = Field(default_factory=list)
    question: str | None = None
    proposed_action: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    action_result: Literal['declined'] | None = None
    incomplete: bool = False
    cart: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
