"""会话视图、轮次声明与经校验的完成响应。"""
from pydantic import BaseModel, Field
from db.base import UtcDateTime
from models.commerce import TurnResponse


class ShoppingSessionView(BaseModel):
    session_id: str
    expires_at: UtcDateTime
    version: int
    state: dict = Field(default_factory=dict)


class TurnClaim(BaseModel):
    session_id: str
    principal_key: str
    turn_id: str
    lease_token: str
    session_version: int
    state: dict = Field(default_factory=dict)


class TurnReplay(BaseModel):
    response: TurnResponse


class TurnCompletion(BaseModel):
    response: TurnResponse
    state: dict = Field(default_factory=dict)
    product_ids: list[str] = Field(default_factory=list)
    sku_ids: list[str] = Field(default_factory=list)
