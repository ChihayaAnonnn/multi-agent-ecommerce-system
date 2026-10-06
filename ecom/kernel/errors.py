"""可信业务事实的公共依赖错误。"""

from ecom.shared.errors import DomainError
from ecom.shared.hashing import JsonObject


class CommerceError(DomainError):
    def __init__(self, code: str, details: JsonObject | None = None) -> None:
        super().__init__(code)
        self.code, self.details = code, details or {}


class CommerceDependencyError(DomainError):
    """可信事实源无法读取。"""
