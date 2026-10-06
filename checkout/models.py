"""不可变交接快照和持久恢复状态。"""
from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import DATETIME6, Base, TimestampMixin


class CheckoutHandoff(TimestampMixin,Base):
    __tablename__='checkout_handoffs'
    __table_args__=(UniqueConstraint('action_id'),CheckConstraint("status IN ('preparing','ready','failed','expired')",name='ck_checkout_handoffs_status'),{'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'})
    id: Mapped[str]=mapped_column(CHAR(36),primary_key=True)
    principal_key: Mapped[str]=mapped_column(CHAR(64))
    cart_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('carts.id'))
    cart_version: Mapped[int]=mapped_column(Integer)
    action_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('pending_actions.id'))
    snapshot_json: Mapped[dict]=mapped_column(JSON)
    snapshot_hash: Mapped[str]=mapped_column(CHAR(64))
    status: Mapped[str]=mapped_column(String(16))
    destination_kind: Mapped[str | None]=mapped_column(String(16))
    destination_ref: Mapped[str | None]=mapped_column(String(512))
    destination_demo: Mapped[bool]=mapped_column(default=False)
    expires_at: Mapped[datetime]=mapped_column(DATETIME6)
