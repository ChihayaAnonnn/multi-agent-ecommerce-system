"""建立库存领域表：库位、库位余额与库存流水，并写入默认库位种子。

Revision ID: 0003_inventory
Revises: 0002_pricing
Create Date: 2026-09-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0003_inventory"
down_revision = "0002_pricing"
branch_labels = None
depends_on = None

TABLE_KWARGS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# 与 inventory.models.DEFAULT_LOCATION_ID 保持一致。
DEFAULT_LOCATION_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "stock_locations",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_locations"),
        sa.UniqueConstraint("code", name="uq_stock_locations_code"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_stock_locations_status"
        ),
        **TABLE_KWARGS,
    )
    # 首版至少一个默认库位；由迁移显式写入，不在应用启动时隐式建数据。
    op.execute(
        sa.text(
            "INSERT INTO stock_locations (id, code, name, status) "
            "VALUES (:id, 'default', '默认库位', 'active')"
        ).bindparams(id=DEFAULT_LOCATION_ID)
    )

    op.create_table(
        "inventory_balances",
        sa.Column("sku_id", sa.CHAR(36), nullable=False),
        sa.Column("location_id", sa.CHAR(36), nullable=False),
        sa.Column("on_hand", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("sku_id", "location_id", name="pk_inventory_balances"),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name="fk_inventory_balances_sku_id_skus"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["stock_locations.id"],
            name="fk_inventory_balances_location_id_stock_locations",
        ),
        sa.CheckConstraint(
            "on_hand >= 0", name="ck_inventory_balances_on_hand_non_negative"
        ),
        sa.CheckConstraint(
            "reserved >= 0 AND reserved <= on_hand",
            name="ck_inventory_balances_reserved_within_on_hand",
        ),
        **TABLE_KWARGS,
    )

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("sku_id", sa.CHAR(36), nullable=False),
        sa.Column("location_id", sa.CHAR(36), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("balance_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_movements"),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name="fk_inventory_movements_sku_id_skus"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["stock_locations.id"],
            name="fk_inventory_movements_location_id_stock_locations",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_inventory_movements_idempotency_key"
        ),
        sa.UniqueConstraint(
            "sku_id",
            "location_id",
            "balance_version",
            name="uq_inventory_movements_sku_id_location_id_balance_version",
        ),
        **TABLE_KWARGS,
    )


def downgrade() -> None:
    op.drop_table("inventory_movements")
    op.drop_table("inventory_balances")
    op.drop_table("stock_locations")
