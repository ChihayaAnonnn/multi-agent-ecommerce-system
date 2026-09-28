"""所有领域 ORM 模型的声明式基类。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import MetaData, func
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# MySQL 的 DATETIME(6)：微秒精度，对应设计稿的 DATETIME(6)。
# 注意不能写成 sa.DateTime(6)：泛型 DateTime 的首个位置参数是 timezone 而不是精度。
DATETIME6 = DATETIME(fsp=6)

# 统一约束命名，便于迁移降级与后续 alembic autogenerate 比对。
# 不含 ck：CHECK 约束一律在模型与迁移中显式命名，避免命名约定重复拼接前缀。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    """应用生成的 UUID 主键。"""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """当前 UTC 时间（naive，与 DATETIME(6) 列一致）。

    业务时间统一由应用写入 UTC，不依赖数据库会话时区。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """领域模型的声明式基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """可变实体共用的创建/更新时间（UTC）。"""

    created_at: Mapped[datetime] = mapped_column(
        DATETIME6, nullable=False, server_default=func.now(6)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME6, nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )
