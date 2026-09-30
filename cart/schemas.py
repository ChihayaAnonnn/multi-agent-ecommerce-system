"""现行购物车视图；历史价格仅用于变化提示。"""
from pydantic import BaseModel, Field
from db.base import UtcDateTime
from commerce_orchestrator.schemas import SKUQuote


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
