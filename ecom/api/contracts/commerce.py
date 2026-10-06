"""交易 API 统一契约，禁止自报主体和未知字段。

Turn 用例 DTO 的唯一归属是 `application.shopping.contracts`，
API 层直接复用，不在本模块重导出。
"""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ecom.modules.events.schemas import EventKind, EventPayload

__all__ = [
    'CommerceInput', 'CreateSessionRequest',
    'CompareRequest', 'ConfirmationRequest', 'CheckoutPreviewRequest',
    'CheckoutHandoffRequest', 'EventRequest', 'ProductContextRequest',
]


class CommerceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateSessionRequest(CommerceInput):
    pass


class CompareRequest(CommerceInput):
    product_ids: list[str] = Field(min_length=2, max_length=3)


class ConfirmationRequest(CommerceInput):
    approved: bool = Field(strict=True)
    idempotency_key: str = Field(min_length=1,max_length=64)


class CheckoutPreviewRequest(CommerceInput):
    cart_version: int=Field(ge=1,strict=True)


class CheckoutHandoffRequest(ConfirmationRequest):
    action_id: str=Field(min_length=1,max_length=36)


class EventRequest(CommerceInput):
    event_id: UUID
    session_id: UUID
    request_id: str=Field(min_length=1,max_length=64)
    kind: EventKind
    payload: EventPayload=Field(default_factory=EventPayload)

class ProductContextRequest(CommerceInput):
    product_id: str = Field(min_length=1,max_length=36)
    sku_id: str = Field(min_length=1,max_length=36)
