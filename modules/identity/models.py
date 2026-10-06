"""身份领域 ORM 模型：账号、登录会话与刷新令牌。

设计约定：
- 主键是应用生成的 UUID `CHAR(36)`，时间列是 UTC `DATETIME(6)`。
- 只保存 Argon2id 哈希与刷新令牌的 SHA-256 哈希，不保存明文。
- 每个登录会话最多一枚活动刷新令牌，由活动行生成列的唯一索引保证；
  应用层再用事务与行锁串行化轮换。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Computed,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base, TimestampMixin
from shared.ids import new_id

DATETIME6 = DATETIME(fsp=6)

USER_STATUSES = ("active", "disabled")
DEMO_SOURCE = "demo"

# 活动刷新令牌：未消费且未撤销时等于所属会话，否则为 NULL。
# MySQL 唯一索引允许多个 NULL，因此同一会话只可能有一行活动令牌。
ACTIVE_SESSION_EXPRESSION = (
    "CASE WHEN consumed_at IS NULL AND revoked_at IS NULL THEN auth_session_id ELSE NULL END"
)


class IdentityUser(TimestampMixin, Base):
    """演示账号；邮箱只是登录名，不作为购物车归属键。"""

    __tablename__ = "identity_users"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    # 规范化规则：去首尾空白 + Unicode NFC + 大小写折叠，不做邮箱商特定的点号折叠。
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=DEMO_SOURCE)

    __table_args__ = (
        UniqueConstraint(
            "email_normalized", name="uq_identity_users_email_normalized"
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_identity_users_status"
        ),
    )


class AuthSession(Base):
    """一次设备登录；绝对到期，不因刷新而无限延长。"""

    __tablename__ = "identity_auth_sessions"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey(
            "identity_users.id", name="fk_identity_auth_sessions_user_id_identity_users"
        ),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME6, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RefreshToken(Base):
    """不透明刷新令牌的哈希行；已消费行保留至会话到期，用于发现重放。"""

    __tablename__ = "identity_refresh_tokens"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_id)
    auth_session_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey(
            "identity_auth_sessions.id",
            # 含目标表名会超过 MySQL 的 64 字符标识符上限，沿用命名约定里的短写法。
            name="fk_identity_refresh_tokens_auth_session_id",
        ),
        nullable=False,
        index=True,
    )
    # SHA-256 十六进制摘要，固定 64 字符；原始令牌不落库。
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DATETIME6, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME6, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME6, nullable=False)
    active_auth_session_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed(ACTIVE_SESSION_EXPRESSION, persisted=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_identity_refresh_tokens_token_hash"),
        UniqueConstraint(
            "active_auth_session_id",
            name="uq_identity_refresh_tokens_active_auth_session_id",
        ),
    )
