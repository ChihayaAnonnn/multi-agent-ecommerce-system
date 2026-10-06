"""商品小计与已确认快照，不包含运费/税费/优惠或最终应付。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ecom.shared.time import UtcDateTime


class CheckoutPreview(BaseModel):
    action_id: str | None = None
    items: list[dict] = Field(default_factory=list)
    current_subtotal: int | None = None
    currency: Literal['CNY']='CNY'
    checkout_eligible: bool = False
    cart_version: int
    checked_at: UtcDateTime
    expires_at: UtcDateTime | None = None
    errors: list[dict] = Field(default_factory=list)
    fee_boundary: str='仅商品小计；运费、税费、优惠和最终应付以宿主结账为准。'


class Destination(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['internal','https']
    ref: str = Field(max_length=512)
    demo: bool=False


class HandoffSnapshot(BaseModel):
    handoff_id: str
    principal_key: str
    issuer: str
    subject: str
    cart_id: str
    cart_version: int
    items: list[dict]
    current_subtotal: int
    currency: Literal['CNY']='CNY'
    expires_at: UtcDateTime


class HandoffResult(BaseModel):
    status: str
    handoff_id: str | None=None
    destination: Destination | None=None
    expires_at: UtcDateTime | None=None
    error_code: str | None=None
    retryable: bool=False
    replayed: bool=False
    preview: CheckoutPreview | None=None


class HandoffAttempt(BaseModel):
    snapshot: HandoffSnapshot
    replayed: bool


__all__ = [
    'CheckoutPreview',
    'Destination',
    'HandoffAttempt',
    'HandoffResult',
    'HandoffSnapshot',
]
