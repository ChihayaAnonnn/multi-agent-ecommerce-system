"""身份领域的对外类型与错误。

后续购物域只消费 `Principal`，不直接拼主体键，也不读取演示注册细节。
错误类型集中在这里，避免 `tokens`/`rate_limit`/`service` 之间相互导入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ecom.shared.errors import DomainError
from ecom.shared.principal import PRINCIPAL_KEY_VERSION as PRINCIPAL_KEY_VERSION
from ecom.shared.principal import Principal as Principal
from ecom.shared.principal import _length_prefixed as _length_prefixed
from ecom.shared.principal import make_principal as make_principal
from ecom.shared.principal import principal_key_for as principal_key_for

# 允许开放演示注册/登录的部署环境；其他环境一律拒绝 source=demo。
DEMO_ENVIRONMENTS = ("development", "test", "demo")

@dataclass(frozen=True)
class UserView:
    """对外的账号视图；演示账号的邮箱一律标记为未验证。"""

    id: str
    email: str
    email_verified: bool
    source: str


@dataclass(frozen=True)
class AuthIssue:
    """一次登录或刷新的结果。

    `refresh_token` 是原始令牌，只在设置 Cookie 的 API 层短暂使用，
    不进日志、不入库。
    """

    access_token: str
    refresh_token: str
    csrf_token: str
    expires_at: datetime
    user_id: str
    session_id: str


class IdentityError(DomainError):
    """身份模块错误基类。"""


class IdentityConfigurationError(IdentityError):
    """密钥、签发方或部署环境配置不合法；不回显密钥内容。"""


class IdentityDependencyError(IdentityError):
    """MySQL 或 Redis 等依赖不可用；调用方应返回 503，不得降级为匿名。"""


class InvalidAccessToken(IdentityError):
    """访问令牌不可信（签名、声明、时效或会话状态任一不通过）。"""


class InvalidCredentials(IdentityError):
    """登录/刷新凭证无效；对外不区分账号不存在、密码错误与账号禁用。"""


class InvalidCsrf(IdentityError):
    """CSRF 值缺失或不匹配；调用方应返回 403。"""


class DemoModeDisabled(IdentityError):
    """当前部署环境不允许演示注册或演示账号登录。"""


class EmailAlreadyRegistered(IdentityError):
    """邮箱已被注册（规范化后唯一）。"""


class InvalidEmail(IdentityError):
    """邮箱不符合演示版登录名的格式约束（例如规范化后超出列宽）。"""


class UnknownAccount(IdentityError):
    """受控运维命令里指定的账号不存在或不是演示账号。"""


class RateLimited(IdentityError):
    """触发限流，调用方应返回 429 与 `Retry-After`。"""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"请求过于频繁，请在 {retry_after_seconds} 秒后重试")
        self.retry_after_seconds = retry_after_seconds


__all__ = [
    'AuthIssue',
    'DEMO_ENVIRONMENTS',
    'DemoModeDisabled',
    'EmailAlreadyRegistered',
    'IdentityConfigurationError',
    'IdentityDependencyError',
    'IdentityError',
    'InvalidAccessToken',
    'InvalidCredentials',
    'InvalidCsrf',
    'InvalidEmail',
    'PRINCIPAL_KEY_VERSION',
    'Principal',
    'RateLimited',
    'UnknownAccount',
    'UserView',
    'make_principal',
    'principal_key_for',
]
