"""所有领域 ORM 模型唯一的声明式基类与时间混入。"""

from datetime import datetime

from sqlalchemy import MetaData, func
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATETIME6 = DATETIME(fsp=6)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


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
