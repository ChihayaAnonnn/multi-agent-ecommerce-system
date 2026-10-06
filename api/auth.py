"""认证端点：注册、登录、CSRF、刷新、退出与当前账号。

HTTP 契约要点：
- 浏览器只在内存保存 access token；刷新令牌放在 `__Host-` + `Secure` +
  `HttpOnly` + `SameSite=Lax` 的 Cookie 里，购物端点仍要求 Bearer。
- 注册/登录/刷新/退出校验 `Origin`，刷新/退出还校验自定义头里的 CSRF 值。
- 所有响应禁止缓存；请求体与日志不记录密码、原始刷新令牌。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from api.identity import (
    CSRF_HEADER_NAME,
    NO_STORE,
    client_ip_for,
    identity_http_config,
    require_identity,
    require_principal,
)
from identity.service import MAX_EMAIL_LENGTH, PasswordPolicyError, normalize_email
from identity.types import (
    AuthIssue,
    DemoModeDisabled,
    EmailAlreadyRegistered,
    IdentityDependencyError,
    InvalidAccessToken,
    InvalidCredentials,
    InvalidCsrf,
    InvalidEmail,
    RateLimited,
    UserView,
)
from shared.principal import Principal
from shared.time import UtcDateTime

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _fail(status_code: int, detail: str, *, retry_after: int | None = None) -> HTTPException:
    """认证端点的错误响应同样禁止缓存。

    长度上限刻意不写成 pydantic 约束：约束失败时框架会把提交值放进 422 响应，
    等于把明文密码回显给调用方。密码策略统一由服务层的 `validate_password` 判定。
    """
    headers = dict(NO_STORE)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


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


def _user_view(view: UserView) -> dict:
    return {
        "user_id": view.id,
        "email": view.email,
        "email_verified": view.email_verified,
        "source": view.source,
    }


def _check_origin(request: Request) -> None:
    """跨站写操作必须来自配置的可信来源。

    浏览器跨站请求一定带 `Origin`，因此「带了但不在白名单」一律拒绝；
    没有 `Origin` 说明不是浏览器发起的跨站表单提交（也不会有 Cookie 被自动携带）。
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    config = identity_http_config(request)
    if not config.allowed_origins or origin not in config.allowed_origins:
        raise _fail(403, "请求来源不被信任")


def _require_csrf(request: Request) -> str:
    value = request.headers.get(CSRF_HEADER_NAME)
    if not value:
        raise _fail(403, "缺少 CSRF 校验值")
    return value


def _refresh_cookie(request: Request) -> str:
    value = request.cookies.get(identity_http_config(request).refresh_cookie_name)
    if not value:
        raise _fail(401, "缺少刷新令牌 Cookie")
    return value


def _set_refresh_cookie(response: Response, request: Request, raw_refresh: str) -> None:
    config = identity_http_config(request)
    response.set_cookie(
        key=config.refresh_cookie_name,
        value=raw_refresh,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_refresh_cookie(response: Response, request: Request) -> None:
    config = identity_http_config(request)
    response.delete_cookie(
        key=config.refresh_cookie_name, path="/", httponly=True, secure=True, samesite="lax"
    )


def _issue_response(issue: AuthIssue, request: Request, status_code: int = 200) -> Response:
    """返回令牌响应并轮换刷新 Cookie；响应禁止缓存。"""
    body = TokenResponse(
        access_token=issue.access_token,
        expires_at=issue.expires_at,
        csrf_token=issue.csrf_token,
        user_id=issue.user_id,
        session_id=issue.session_id,
    )
    response = Response(
        content=body.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
        headers=dict(NO_STORE),
    )
    _set_refresh_cookie(response, request, issue.refresh_token)
    return response


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    payload: RegisterRequest, request: Request, response: Response
) -> RegisterResponse:
    """演示注册：不发送邮件，也不自动登录。"""
    _check_origin(request)
    service = require_identity(request)
    try:
        view = await service.register_demo(
            payload.email, payload.password, client_ip_for(request)
        )
    except DemoModeDisabled as exc:
        raise _fail(403, str(exc)) from exc
    except EmailAlreadyRegistered as exc:
        raise _fail(409, str(exc)) from exc
    except (PasswordPolicyError, InvalidEmail) as exc:
        raise _fail(422, str(exc)) from exc
    except RateLimited as exc:
        raise _fail(429, str(exc), retry_after=exc.retry_after_seconds) from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc
    response.headers.update(NO_STORE)
    return RegisterResponse(**_user_view(view))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request) -> Response:
    """密码登录；成功后才创建登录会话并设置刷新 Cookie。"""
    _check_origin(request)
    service = require_identity(request)
    try:
        issue = await service.login(payload.email, payload.password, client_ip_for(request))
    except InvalidCredentials as exc:
        raise _fail(401, str(exc)) from exc
    except RateLimited as exc:
        raise _fail(429, str(exc), retry_after=exc.retry_after_seconds) from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc
    return _issue_response(issue, request)


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(request: Request) -> Response:
    """凭刷新 Cookie 取当前会话的 CSRF 值；不轮换刷新令牌。"""
    service = require_identity(request)
    try:
        value = await service.csrf_for_refresh(_refresh_cookie(request))
    except (InvalidCredentials, InvalidAccessToken) as exc:
        # 账号被禁用等账号级失效也归到 401，不能变成未映射的 500
        raise _fail(401, "刷新令牌无效或已失效") from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc
    return Response(
        content=CsrfResponse(csrf_token=value).model_dump_json(),
        media_type="application/json",
        headers=dict(NO_STORE),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request) -> Response:
    """轮换刷新令牌并返回新的访问令牌。"""
    _check_origin(request)
    service = require_identity(request)
    try:
        issue = await service.refresh(
            _refresh_cookie(request), _require_csrf(request), client_ip_for(request)
        )
    except InvalidCsrf as exc:
        raise _fail(403, str(exc)) from exc
    except (InvalidCredentials, InvalidAccessToken) as exc:
        raise _fail(401, "刷新令牌无效或已失效") from exc
    except RateLimited as exc:
        raise _fail(429, str(exc), retry_after=exc.retry_after_seconds) from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc
    return _issue_response(issue, request)


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    """撤销当前登录会话并清除刷新 Cookie。"""
    _check_origin(request)
    service = require_identity(request)
    try:
        await service.logout(_refresh_cookie(request), _require_csrf(request))
    except InvalidCsrf as exc:
        raise _fail(403, str(exc)) from exc
    except (InvalidCredentials, InvalidAccessToken) as exc:
        raise _fail(401, "刷新令牌无效或已失效") from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc

    response = Response(status_code=204, headers=dict(NO_STORE))
    _clear_refresh_cookie(response, request)
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    """返回当前主体对应的账号；演示账号邮箱一律标记为未验证。"""
    service = require_identity(request)
    try:
        view = await service.get_me(principal)
    except InvalidAccessToken as exc:
        raise _fail(401, "访问令牌无效或已失效") from exc
    except IdentityDependencyError as exc:
        raise _fail(503, "身份服务不可用") from exc
    return Response(
        content=MeResponse(**_user_view(view)).model_dump_json(),
        media_type="application/json",
        headers=dict(NO_STORE),
    )
