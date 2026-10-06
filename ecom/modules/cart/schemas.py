"""现行购物车视图；历史价格仅用于变化提示。"""
from typing import Literal

from pydantic import BaseModel, Field

from ecom.kernel.quotes import SKUQuote
from ecom.shared.hashing import JsonObject
from ecom.shared.time import UtcDateTime


class CartLineSnapshot(BaseModel):
    line_id: str
    sku_id: str
    quantity: int
    product_name_snapshot: str
    sku_options_snapshot: list[JsonObject]
    image_url_snapshot: str | None
    last_seen_unit_amount_minor: int | None
    last_seen_price_version: int | None


class CartSnapshot(BaseModel):
    cart_id: str
    principal_key: str
    version: int
    lines: list[CartLineSnapshot]


class PendingActionRecord(BaseModel):
    action_id: str
    session_id: str
    principal_key: str
    cart_id: str
    kind: str
    proposal_hash: str
    status: str
    source_turn_id: str | None
    confirmation_key: str | None
    confirmation_request_hash: str | None
    expected_cart_version: int
    payload: JsonObject
    summary: JsonObject
    expires_at: UtcDateTime
    created_at: UtcDateTime
    result: JsonObject | None


class ActionProposalCommand(BaseModel):
    kind: Literal['add', 'set_quantity', 'remove']
    target: JsonObject
    expected_cart_version: int
    source_turn_id: str | None = None


class CartLineView(BaseModel):
    line_id: str
    sku_id: str
    quantity: int
    product_name: str
    sku_options: list[dict] = Field(default_factory=list)
    quote: SKUQuote
    reason: str
    price_changed: bool = False


class CartView(BaseModel):
    cart_id: str
    version: int
    items: list[CartLineView] = Field(default_factory=list)
    current_subtotal: int | None
    currency: str = 'CNY'
    checkout_eligible: bool
    checked_at: UtcDateTime


class PendingActionView(BaseModel):
    action_id: str
    kind: str
    status: str
    expected_cart_version: int
    payload: dict
    summary: dict
    expires_at: UtcDateTime


class CartConfirmationView(BaseModel):
    status: str
    cart: CartView | None = None
    replayed: bool = False
    error_code: str | None = None
    proposed_action: PendingActionView | None = None


__all__ = [
    'ActionProposalCommand',
    'CartConfirmationView',
    'CartLineSnapshot',
    'CartLineView',
    'CartSnapshot',
    'CartView',
    'PendingActionRecord',
    'PendingActionView',
]
