"""共享数据库基础设施：ORM 基类与异步会话工厂。

本包只提供基础设施，不放业务规则；业务规则属于 catalog/、pricing/、inventory/ 各领域模块。
"""

from .base import Base
from .session import create_session_factory

__all__ = ["Base", "create_session_factory"]
