"""共享领域错误基类。"""


class DomainError(Exception):
    """不携带 HTTP 或具体业务字段的领域错误。"""
