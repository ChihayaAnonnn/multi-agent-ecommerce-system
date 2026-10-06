"""库存领域 ORM 模型：库位、库位余额与库存流水。

设计约定：
- 可售量 = on_hand - reserved；第一阶段不开放预留写入，reserved 保持 0。
- 只有库存模块可以写 inventory_balances，且调整与流水同事务提交。
- 流水是不可变记录，只保留事件时间 occurred_at。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from ecom.shared.db import Base, TimestampMixin
from ecom.shared.ids import new_id

DATETIME6 = DATETIME(fsp=6)

# 迁移种子写入的默认库位；固定 ID 保证各环境一致。
DEFAULT_LOCATION_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_LOCATION_CODE = "default"
LOCATION_STATUSES = ("active", "inactive")


class StockLocation(TimestampMixin, Base):
    """库存库位；首版至少一个默认库位。"""

    __tablename__ = "stock_locations"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")

    __table_args__ = (
        UniqueConstraint("code", name="uq_stock_locations_code"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_stock_locations_status"
        ),
    )


class InventoryBalance(TimestampMixin, Base):
    """SKU 在某个库位的余额；可售量为 on_hand - reserved。"""

    __tablename__ = "inventory_balances"

    sku_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("skus.id", name="fk_inventory_balances_sku_id_skus"),
        primary_key=True,
    )
    location_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("stock_locations.id", name="fk_inventory_balances_location_id_stock_locations"),
        primary_key=True,
    )
    on_hand: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "on_hand >= 0", name="ck_inventory_balances_on_hand_non_negative"
        ),
        CheckConstraint(
            "reserved >= 0 AND reserved <= on_hand",
            name="ck_inventory_balances_reserved_within_on_hand",
        ),
    )


class InventoryMovement(Base):
    """库存调整流水；幂等键全局唯一，重复请求不会再次加减。"""

    __tablename__ = "inventory_movements"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    sku_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("skus.id", name="fk_inventory_movements_sku_id_skus"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey(
            "stock_locations.id", name="fk_inventory_movements_location_id_stock_locations"
        ),
        nullable=False,
    )
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    balance_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_inventory_movements_idempotency_key"),
        UniqueConstraint(
            "sku_id",
            "location_id",
            "balance_version",
            name="uq_inventory_movements_sku_id_location_id_balance_version",
        ),
    )
