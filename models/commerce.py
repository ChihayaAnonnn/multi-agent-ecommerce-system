"""交易 API 统一契约，禁止自报主体和未知字段。"""
from pydantic import BaseModel, ConfigDict, Field

from commerce_orchestrator.contracts import TurnRequest, TurnResponse

__all__ = [
    'CommerceInput', 'CreateSessionRequest', 'TurnRequest', 'TurnResponse',
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

from uuid import UUID
from commerce_events.schemas import EventKind,EventPayload


class EventRequest(CommerceInput):
    event_id: UUID
    session_id: UUID
    request_id: str=Field(min_length=1,max_length=64)
    kind: EventKind
    payload: EventPayload=Field(default_factory=EventPayload)

class ProductContextRequest(CommerceInput):
    product_id: str = Field(min_length=1,max_length=36)
    sku_id: str = Field(min_length=1,max_length=36)
