"""会话视图、轮次声明与仅承载 JSON 的完成及回放封装。"""
import json

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, field_validator

from ecom.shared.hashing import JsonObject
from ecom.shared.time import UtcDateTime

__all__ = [
    'ShoppingSessionView', 'TurnClaim', 'TurnReplay', 'TurnCompletion',
    'validate_json_object',
]

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def validate_json_object(value: JsonObject) -> JsonObject:
    validated = _JSON_OBJECT.validate_python(value, strict=True)
    json.dumps(validated, allow_nan=False)
    return validated


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
    session_id: str
    turn_id: str
    response: JsonObject

    @field_validator('response', mode='before')
    @classmethod
    def validate_response(cls, value: JsonObject) -> JsonObject:
        return validate_json_object(value)


class TurnCompletion(BaseModel):
    session_id: str
    turn_id: str
    response: JsonObject
    state: JsonObject = Field(default_factory=dict)
    product_ids: list[str] = Field(default_factory=list)
    sku_ids: list[str] = Field(default_factory=list)

    @field_validator('response', 'state', mode='before')
    @classmethod
    def validate_json(cls, value: JsonObject) -> JsonObject:
        return validate_json_object(value)
