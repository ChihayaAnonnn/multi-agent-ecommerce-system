"""价格领域 ORM 模型：现行价格与追加式价格变更记录。

设计约定：
- 金额以整数分（BIGINT）存储，拒绝浮点输入。
- 每个 SKU 每种币种只有一行现行价格，更新时递增版本。
- 价格变更记录是只追加流水，与价格更新在同一事务内提交。
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

from ecom.shared.db import Base
from ecom.shared.ids import new_id

DATETIME6 = DATETIME(fsp=6)

# 首版只接受人民币；多币种需要单独设计，不能靠放宽本约束暗中支持。
CURRENCY = "CNY"


class SKUPrice(Base):
    """SKU 的现行价格（每个 SKU 每种币种一行）。"""

    __tablename__ = "sku_prices"

    sku_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("skus.id", name="fk_sku_prices_sku_id_skus"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    effective_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)

    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_sku_prices_amount_minor"),
    )


class SKUPriceChange(Base):
    """价格变更流水：不可变，只保留事件时间。"""

    __tablename__ = "sku_price_changes"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    sku_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("skus.id", name="fk_sku_price_changes_sku_id_skus"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    old_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "sku_id",
            "currency",
            "price_version",
            name="uq_sku_price_changes_sku_id_currency_price_version",
        ),
    )
