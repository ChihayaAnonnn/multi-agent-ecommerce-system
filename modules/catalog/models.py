"""目录领域 ORM 模型：类目、商品（Product）、可售单元（SKU）。

设计约定：
- `Product` 是展示、搜索与比较的商品族；`SKU` 是可被购买的确定规格。
- 商品表不持久化价格与库存，价格属于 pricing、库存属于 inventory。
- 主键为应用生成的 UUID 字符串；可售单元与关联表的约束名在模型与迁移中保持一致。
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, TimestampMixin
from shared.ids import new_id

PRODUCT_STATUSES = ("draft", "active", "archived")
SKU_STATUSES = ("active", "inactive")
CATEGORY_STATUSES = ("active", "inactive")
ATTRIBUTE_VALUE_TYPES = ("text", "number", "boolean")


class Category(TimestampMixin, Base):
    """商品类目，单父级；循环引用由目录服务拒绝。"""

    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    parent_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("categories.id", name="fk_categories_parent_id_categories"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("code", name="uq_categories_code"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_categories_status"
        ),
    )


class CategoryAttribute(TimestampMixin, Base):
    """类目可比较属性的定义；属性值先存于 products.attributes JSON。"""

    __tablename__ = "category_attributes"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    category_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("categories.id", name="fk_category_attributes_category_id_categories"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_comparable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    is_filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("category_id", "code", name="uq_category_attributes_category_id_code"),
        CheckConstraint(
            "value_type IN ('text', 'number', 'boolean')",
            name="ck_category_attributes_value_type",
        ),
    )


class Product(TimestampMixin, Base):
    """商品族（商品详情页维度），不存价格、库存和卖家。"""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    category_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("categories.id", name="fk_products_category_id_categories"), nullable=False
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("product_code", name="uq_products_product_code"),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')", name="ck_products_status"
        ),
        Index("ix_products_category_id_status", "category_id", "status"),
        Index("ix_products_status_updated_at", "status", "updated_at"),
    )


class ProductOption(TimestampMixin, Base):
    """商品购买规格（如颜色、容量）；签名依赖选项 ID 而非展示顺序。"""

    __tablename__ = "product_options"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("products.id", name="fk_product_options_product_id_products"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("product_id", "code", name="uq_product_options_product_id_code"),
        UniqueConstraint("product_id", "sort_order", name="uq_product_options_product_id_sort_order"),
    )


class OptionValue(TimestampMixin, Base):
    """某个购买规格下的可选值。"""

    __tablename__ = "option_values"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    option_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("product_options.id", name="fk_option_values_option_id_product_options"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_value: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("option_id", "code", name="uq_option_values_option_id_code"),
    )


class SKU(TimestampMixin, Base):
    """可售单元；`option_signature` 是按选项 ID 排序后组合的 SHA-256，无选项为 `default`。"""

    __tablename__ = "skus"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id", name="fk_skus_product_id_products"), nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    option_signature: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("sku_code", name="uq_skus_sku_code"),
        UniqueConstraint("product_id", "option_signature", name="uq_skus_product_id_option_signature"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_skus_status"),
        Index("ix_skus_product_id_status", "product_id", "status"),
    )


class SKUOptionValue(Base):
    """SKU 选中的规格值；选项与值必须同属该 SKU 的 Product，由目录服务校验。"""

    __tablename__ = "sku_option_values"

    sku_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("skus.id", name="fk_sku_option_values_sku_id_skus"), primary_key=True
    )
    option_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("product_options.id", name="fk_sku_option_values_option_id_product_options"),
        primary_key=True,
    )
    value_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("option_values.id", name="fk_sku_option_values_value_id_option_values"),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_sku_option_values_value_id", "value_id"),
    )


class ProductMedia(TimestampMixin, Base):
    """商品或 SKU 的媒体资源；`sku_id` 非空时须归属同一 Product。"""

    __tablename__ = "product_media"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("products.id", name="fk_product_media_product_id_products"), nullable=False
    )
    sku_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("skus.id", name="fk_product_media_sku_id_skus"), nullable=True
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        Index("ix_product_media_product_id_sort_order", "product_id", "sort_order"),
    )
