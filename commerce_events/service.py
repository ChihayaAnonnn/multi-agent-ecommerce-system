"""采集独立提交；失败可观察，但不回滚既有购物事务。"""
from typing import Literal
from uuid import UUID
import structlog
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from sqlalchemy.exc import SQLAlchemyError,IntegrityError
from commerce_events.models import CommerceEvent
from shopping_session.service import CommerceError,ShoppingSessionService
from identity.types import Principal
from commerce_orchestrator.utils import request_hash
from db.base import utcnow

logger=structlog.get_logger()
from commerce_events.schemas import EventKind,EventPayload,EventReceipt,KINDS


class CommerceEventService:
    def __init__(self,factory,sessions: ShoppingSessionService):
        self.factory,self.sessions=factory,sessions

    @staticmethod
    def existing_receipt(row: CommerceEvent,principal: Principal,digest: str) -> EventReceipt:
        if row.principal_key!=principal.principal_key:
            raise CommerceError('not_found',404)
        if row.request_hash!=digest:
            raise CommerceError('idempotency_conflict')
        return EventReceipt(accepted=True,duplicate=True)

    async def record(self,principal: Principal,session_id: str,event_id: str,request_id: str,kind: str,payload: dict) -> EventReceipt:
        try:
            event_id=str(UUID(event_id))
            safe=EventPayload.model_validate(payload).model_dump(mode='json',exclude_none=True)
        except (ValueError,ValidationError) as exc:
            raise CommerceError('invalid_event_input',422) from exc
        if kind not in KINDS or not 1<=len(request_id)<=64:
            raise CommerceError('invalid_event_input',422)
        digest=request_hash({'session_id':session_id,'request_id':request_id,'kind':kind,'payload':safe})
        try:
            async with self.factory() as db,db.begin():
                await self.sessions.owned_row(db,session_id,principal.principal_key)
                existing=await db.get(CommerceEvent,event_id)
                if existing:
                    return self.existing_receipt(existing,principal,digest)
                db.add(CommerceEvent(id=event_id,principal_key=principal.principal_key,session_id=session_id,
                    request_id=request_id,request_hash=digest,kind=kind,payload_json=safe,created_at=utcnow()))
                await db.flush()
            return EventReceipt(accepted=True)
        except IntegrityError:
            try:
                async with self.factory() as db:
                    existing=await db.get(CommerceEvent,event_id)
                    if existing:
                        return self.existing_receipt(existing,principal,digest)
            except SQLAlchemyError:
                pass
        except SQLAlchemyError:
            pass
        logger.warning('commerce.event_unavailable',event_id=event_id,session_id=session_id,request_id=request_id)
        return EventReceipt(accepted=False,error_code='event_unavailable')
