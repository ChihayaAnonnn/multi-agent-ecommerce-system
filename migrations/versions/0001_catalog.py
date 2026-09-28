"""建立目录领域 8 张表：类目、属性定义、商品、选项、选项值、SKU、SKU 选项值、媒体。

Revision ID: 0001_catalog
Revises:
Create Date: 2026-09-28

约定：MySQL 8.0 / InnoDB / utf8mb4；主键为应用生成的 UUID CHAR(36)；
约束名与 catalog/models.py 中显式声明的名称保持一致。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0001_catalog"
down_revision = None
branch_labels = None
depends_on = None

TABLE_KWARGS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

CREATED_AT = sa.Column(
    "created_at",
    DATETIME(fsp=6),
    nullable=False,
    server_default=sa.text("CURRENT_TIMESTAMP(6)"),
)
UPDATED_AT = sa.Column(
    "updated_at",
    DATETIME(fsp=6),
    nullable=False,
    server_default=sa.text("CURRENT_TIMESTAMP(6)"),
)


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("parent_id", sa.CHAR(36), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("code", name="uq_categories_code"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["categories.id"], name="fk_categories_parent_id_categories"
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_categories_status"),
        **TABLE_KWARGS,
    )

    op.create_table(
        "category_attributes",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("category_id", sa.CHAR(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("is_comparable", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_filterable", sa.Boolean(), nullable=False, server_default="0"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_category_attributes"),
        sa.UniqueConstraint("category_id", "code", name="uq_category_attributes_category_id_code"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_category_attributes_category_id_categories"
        ),
        sa.CheckConstraint(
            "value_type IN ('text', 'number', 'boolean')",
            name="ck_category_attributes_value_type",
        ),
        **TABLE_KWARGS,
    )

    op.create_table(
        "products",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("category_id", sa.CHAR(36), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("product_code", name="uq_products_product_code"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_products_category_id_categories"
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_products_status"),
        **TABLE_KWARGS,
    )
    op.create_index("ix_products_category_id_status", "products", ["category_id", "status"])
    op.create_index("ix_products_status_updated_at", "products", ["status", "updated_at"])

    op.create_table(
        "product_options",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("product_id", sa.CHAR(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_product_options"),
        sa.UniqueConstraint("product_id", "code", name="uq_product_options_product_id_code"),
        sa.UniqueConstraint(
            "product_id", "sort_order", name="uq_product_options_product_id_sort_order"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_product_options_product_id_products"
        ),
        **TABLE_KWARGS,
    )

    op.create_table(
        "option_values",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("option_id", sa.CHAR(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_value", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_option_values"),
        sa.UniqueConstraint("option_id", "code", name="uq_option_values_option_id_code"),
        sa.ForeignKeyConstraint(
            ["option_id"], ["product_options.id"], name="fk_option_values_option_id_product_options"
        ),
        **TABLE_KWARGS,
    )

    op.create_table(
        "skus",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("product_id", sa.CHAR(36), nullable=False),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("option_signature", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_skus"),
        sa.UniqueConstraint("sku_code", name="uq_skus_sku_code"),
        sa.UniqueConstraint(
            "product_id", "option_signature", name="uq_skus_product_id_option_signature"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_skus_product_id_products"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_skus_status"),
        **TABLE_KWARGS,
    )
    op.create_index("ix_skus_product_id_status", "skus", ["product_id", "status"])

    op.create_table(
        "sku_option_values",
        sa.Column("sku_id", sa.CHAR(36), nullable=False),
        sa.Column("option_id", sa.CHAR(36), nullable=False),
        sa.Column("value_id", sa.CHAR(36), nullable=False),
        sa.PrimaryKeyConstraint("sku_id", "option_id", name="pk_sku_option_values"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name="fk_sku_option_values_sku_id_skus"),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["product_options.id"],
            name="fk_sku_option_values_option_id_product_options",
        ),
        sa.ForeignKeyConstraint(
            ["value_id"], ["option_values.id"], name="fk_sku_option_values_value_id_option_values"
        ),
        **TABLE_KWARGS,
    )
    op.create_index("ix_sku_option_values_value_id", "sku_option_values", ["value_id"])

    op.create_table(
        "product_media",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("product_id", sa.CHAR(36), nullable=False),
        sa.Column("sku_id", sa.CHAR(36), nullable=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("alt_text", sa.String(255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        CREATED_AT,
        UPDATED_AT,
        sa.PrimaryKeyConstraint("id", name="pk_product_media"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_product_media_product_id_products"
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name="fk_product_media_sku_id_skus"),
        **TABLE_KWARGS,
    )
    op.create_index(
        "ix_product_media_product_id_sort_order", "product_media", ["product_id", "sort_order"]
    )


def downgrade() -> None:
    # 按依赖倒序删表；DROP TABLE 会一并删除索引与外键。
    # 不要单独 drop_index：MySQL 会用这些索引满足外键，单独删除会被拒绝（错误 1553）。
    op.drop_table("product_media")
    op.drop_table("sku_option_values")
    op.drop_table("skus")
    op.drop_table("option_values")
    op.drop_table("product_options")
    op.drop_table("products")
    op.drop_table("category_attributes")
    op.drop_table("categories")
