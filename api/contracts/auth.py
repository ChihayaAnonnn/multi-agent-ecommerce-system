"""认证端点的 HTTP 请求/响应契约。

密码字段刻意只写最小长度、不写上限：约束失败时框架会把提交值放进 422 响应，
等于把明文密码回显给调用方；密码策略统一由服务层的 `validate_password` 判定
（详见 `api.routes.auth._fail` 的说明）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from modules.identity.service import MAX_EMAIL_LENGTH, normalize_email
from shared.time import UtcDateTime


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _looks_like_login_name(cls, value: str) -> str:
        # 演示邮箱只作登录名，这里只挡住明显不可能入座的输入。
        # 长度按规范化后的结果判断：大小写折叠可能让结果变长。
        candidate = value.strip()
        if "@" not in candidate or candidate.startswith("@") or candidate.endswith("@"):
            raise ValueError("邮箱格式不正确")
        if any(character.isspace() for character in candidate):
            raise ValueError("邮箱不能包含空白字符")
        if len(normalize_email(candidate)) > MAX_EMAIL_LENGTH:
            raise ValueError("邮箱过长")
        return candidate


class LoginRequest(RegisterRequest):
    pass


class RegisterResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    source: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: UtcDateTime
    csrf_token: str
    user_id: str
    session_id: str


class CsrfResponse(BaseModel):
    csrf_token: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    source: str
