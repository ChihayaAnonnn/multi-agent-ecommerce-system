"""建立身份领域表：账号、登录会话与刷新令牌。

Revision ID: 0004_identity
Revises: 0003_inventory
Create Date: 2026-09-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0004_identity"
down_revision = "0003_inventory"
branch_labels = None
depends_on = None

TABLE_KWARGS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# 与 identity.models.ACTIVE_SESSION_EXPRESSION 保持一致：
# 未消费且未撤销的刷新令牌才占用「每会话一枚活动令牌」的唯一槽位。
ACTIVE_SESSION_EXPRESSION = (
    "CASE WHEN consumed_at IS NULL AND revoked_at IS NULL THEN auth_session_id ELSE NULL END"
)


def upgrade() -> None:
    op.create_table(
        "identity_users",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(16), nullable=False, server_default="demo"),
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
        sa.PrimaryKeyConstraint("id", name="pk_identity_users"),
        sa.UniqueConstraint(
            "email_normalized", name="uq_identity_users_email_normalized"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_identity_users_status"
        ),
        **TABLE_KWARGS,
    )

    op.create_table(
        "identity_auth_sessions",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("last_used_at", DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("revoked_at", DATETIME(fsp=6), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_identity_auth_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity_users.id"],
            name="fk_identity_auth_sessions_user_id_identity_users",
        ),
        **TABLE_KWARGS,
    )
    op.create_index(
        "ix_identity_auth_sessions_user_id",
        "identity_auth_sessions",
        ["user_id"],
    )

    op.create_table(
        "identity_refresh_tokens",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("auth_session_id", sa.CHAR(36), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("expires_at", DATETIME(fsp=6), nullable=False),
        sa.Column("consumed_at", DATETIME(fsp=6), nullable=True),
        sa.Column("revoked_at", DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column(
            "active_auth_session_id",
            sa.CHAR(36),
            sa.Computed(ACTIVE_SESSION_EXPRESSION, persisted=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_identity_refresh_tokens"),
        sa.ForeignKeyConstraint(
            ["auth_session_id"],
            ["identity_auth_sessions.id"],
            # 含目标表名会超过 MySQL 的 64 字符标识符上限，沿用命名约定里的短写法。
            name="fk_identity_refresh_tokens_auth_session_id",
        ),
        sa.UniqueConstraint("token_hash", name="uq_identity_refresh_tokens_token_hash"),
        # 唯一索引允许多个 NULL：同一会话最多一行活动令牌。
        sa.UniqueConstraint(
            "active_auth_session_id",
            name="uq_identity_refresh_tokens_active_auth_session_id",
        ),
        **TABLE_KWARGS,
    )
    op.create_index(
        "ix_identity_refresh_tokens_auth_session_id",
        "identity_refresh_tokens",
        ["auth_session_id"],
    )


def downgrade() -> None:
    op.drop_table("identity_refresh_tokens")
    op.drop_table("identity_auth_sessions")
    op.drop_table("identity_users")
