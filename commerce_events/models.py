"""事件只保存有限属性，不存凭证或完整聊天。"""
from datetime import datetime
from sqlalchemy import CHAR,String,JSON,ForeignKey,Index
from sqlalchemy.orm import Mapped,mapped_column
from db.base import Base,DATETIME6


class CommerceEvent(Base):
    __tablename__='commerce_events'
    __table_args__=(Index('ix_commerce_events_principal_session','principal_key','session_id'),{'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'})
    id: Mapped[str]=mapped_column(CHAR(36),primary_key=True)
    principal_key: Mapped[str]=mapped_column(CHAR(64))
    session_id: Mapped[str]=mapped_column(CHAR(36),ForeignKey('commerce_sessions.id'))
    request_id: Mapped[str]=mapped_column(String(64))
    request_hash: Mapped[str]=mapped_column(CHAR(64))
    kind: Mapped[str]=mapped_column(String(32))
    payload_json: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DATETIME6)
