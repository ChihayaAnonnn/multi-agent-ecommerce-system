"""一主体一车、一会话一待决动作；数据库约束覆盖多进程。"""
from datetime import datetime
from sqlalchemy import CHAR, String, Integer, BigInteger, JSON, ForeignKey, UniqueConstraint, CheckConstraint, Computed
from sqlalchemy.orm import Mapped, mapped_column
from db.base import Base, TimestampMixin, DATETIME6


class Cart(TimestampMixin, Base):
    __tablename__='carts'
    __table_args__=(UniqueConstraint('principal_key'),{'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'})
    id: Mapped[str]=mapped_column(CHAR(36),primary_key=True)
    principal_key: Mapped[str]=mapped_column(CHAR(64))
    version: Mapped[int]=mapped_column(Integer,default=1)


class CartLine(TimestampMixin, Base):
    __tablename__='cart_lines'
    __table_args__=(UniqueConstraint('cart_id','sku_id'),CheckConstraint('quantity > 0',name='ck_cart_lines_quantity'),{'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'})
    id: Mapped[str]=mapped_column(CHAR(36),primary_key=True)
    cart_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('carts.id'))
    sku_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('skus.id'))
    quantity: Mapped[int]=mapped_column(Integer)
    product_name_snapshot: Mapped[str]=mapped_column(String(255))
    sku_options_snapshot: Mapped[list]=mapped_column(JSON)
    image_url_snapshot: Mapped[str | None]=mapped_column(String(512))
    last_seen_unit_amount_minor: Mapped[int | None]=mapped_column(BigInteger)
    last_seen_price_version: Mapped[int | None]=mapped_column(Integer)


class PendingAction(TimestampMixin, Base):
    __tablename__='pending_actions'
    __table_args__=(UniqueConstraint('session_id','source_turn_id'),UniqueConstraint('principal_key','confirmation_key'),UniqueConstraint('active_session_id'),
        CheckConstraint("kind IN ('add','set_quantity','remove','checkout_handoff')",name='ck_pending_actions_kind'),
        CheckConstraint("status IN ('pending','declined','consumed','invalidated','expired')",name='ck_pending_actions_status'),
        {'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'})
    id: Mapped[str]=mapped_column(CHAR(36),primary_key=True)
    session_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('commerce_sessions.id'))
    source_turn_id: Mapped[str | None]=mapped_column(String(64))
    principal_key: Mapped[str]=mapped_column(CHAR(64))
    cart_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('carts.id'))
    kind: Mapped[str]=mapped_column(String(32))
    payload_json: Mapped[dict]=mapped_column(JSON)
    proposal_hash: Mapped[str]=mapped_column(CHAR(64))
    expected_cart_version: Mapped[int]=mapped_column(Integer)
    quote_snapshot_json: Mapped[dict]=mapped_column(JSON)
    status: Mapped[str]=mapped_column(String(16),default='pending')
    expires_at: Mapped[datetime]=mapped_column(DATETIME6)
    confirmation_key: Mapped[str | None]=mapped_column(String(64))
    confirmation_request_hash: Mapped[str | None]=mapped_column(CHAR(64))
    result_json: Mapped[dict | None]=mapped_column(JSON)
    active_session_id: Mapped[str | None]=mapped_column(CHAR(36),Computed("CASE WHEN status='pending' THEN session_id ELSE NULL END",persisted=True))
