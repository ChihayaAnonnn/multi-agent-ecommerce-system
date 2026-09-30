"""有限事件 DTO；不依赖业务服务，避免跨领域导入循环。"""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field

EventKind=Literal['impression','select_sku','compare','propose_action','confirm','cart_updated','handoff','error','retry']
KINDS={'impression','select_sku','compare','propose_action','confirm','cart_updated','handoff','error','retry'}


class EventPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_id: UUID | None=None
    sku_id: UUID | None=None
    action_id: UUID | None=None
    handoff_id: UUID | None=None
    position: int | None=Field(default=None,ge=0,le=2,strict=True)
    status: Literal['clarify','shortlist','compare','confirm_action','cart_updated','handoff','unsupported','unavailable','available','out_of_stock','unknown','declined'] | None=None
    demo: bool | None=Field(default=None,strict=True)


class EventReceipt(BaseModel):
    accepted: bool
    duplicate: bool=False
    error_code: str | None=None

