"""会话是跨轮事实源；租约阻止旧执行者提交。"""
from datetime import datetime
from sqlalchemy import CHAR, String, Integer, JSON, ForeignKey, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from db.base import Base, TimestampMixin, DATETIME6


class CommerceSession(TimestampMixin, Base):
    __tablename__ = 'commerce_sessions'
    __table_args__ = (Index('ix_commerce_sessions_principal_expiry', 'principal_key', 'expires_at'),
        {'mysql_engine':'InnoDB', 'mysql_charset':'utf8mb4'})
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    principal_key: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(String(16), default='active')
    expires_at: Mapped[datetime] = mapped_column(DATETIME6)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_turn_id: Mapped[str | None] = mapped_column(String(64))
    turn_lease_token: Mapped[str | None] = mapped_column(CHAR(36))
    turn_lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME6)


class CommerceTurn(Base):
    __tablename__ = 'commerce_turns'
    __table_args__ = (CheckConstraint("status IN ('processing','completed')", name='ck_commerce_turns_status'),
        {'mysql_engine':'InnoDB', 'mysql_charset':'utf8mb4'})
    session_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey('commerce_sessions.id'), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(String(16))
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME6)


class SeenProduct(Base):
    __tablename__ = 'session_seen_products'
    session_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey('commerce_sessions.id'), primary_key=True)
    product_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey('products.id'), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DATETIME6)


class SeenSKU(Base):
    __tablename__ = 'session_seen_skus'
    session_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey('commerce_sessions.id'), primary_key=True)
    sku_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey('skus.id'), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DATETIME6)
