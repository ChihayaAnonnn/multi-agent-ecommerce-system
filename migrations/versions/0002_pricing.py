"""建立价格领域表：SKU 现行价格与追加式价格变更流水。

Revision ID: 0002_pricing
Revises: 0001_catalog
Create Date: 2026-09-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0002_pricing"
down_revision = "0001_catalog"
branch_labels = None
depends_on = None

TABLE_KWARGS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "sku_prices",
        sa.Column("sku_id", sa.CHAR(36), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_at", DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("sku_id", "currency", name="pk_sku_prices"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], name="fk_sku_prices_sku_id_skus"),
        sa.CheckConstraint("amount_minor >= 0", name="ck_sku_prices_amount_minor"),
        **TABLE_KWARGS,
    )

    op.create_table(
        "sku_price_changes",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("sku_id", sa.CHAR(36), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("old_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("new_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("price_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("changed_at", DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sku_price_changes"),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name="fk_sku_price_changes_sku_id_skus"
        ),
        sa.UniqueConstraint(
            "sku_id",
            "currency",
            "price_version",
            name="uq_sku_price_changes_sku_id_currency_price_version",
        ),
        **TABLE_KWARGS,
    )


def downgrade() -> None:
    op.drop_table("sku_price_changes")
    op.drop_table("sku_prices")
