"""采集独立提交；失败可观察，但不回滚既有购物事务。"""
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ecom.kernel.errors import CommerceError
from ecom.modules.events.models import CommerceEvent
from ecom.modules.events.schemas import KINDS, EventPayload, EventReceipt
from ecom.modules.sessions.service import ShoppingSessionService
from ecom.shared.hashing import request_hash
from ecom.shared.principal import Principal
from ecom.shared.time import utcnow

logger=structlog.get_logger()


class CommerceEventService:
    def __init__(self,factory,sessions: ShoppingSessionService):
        self.factory,self.sessions=factory,sessions

    @staticmethod
    def existing_receipt(row: CommerceEvent,principal: Principal,digest: str) -> EventReceipt:
        if row.principal_key!=principal.principal_key:
            raise CommerceError('not_found')
        if row.request_hash!=digest:
            raise CommerceError('idempotency_conflict')
        return EventReceipt(accepted=True,duplicate=True)

    async def record(self,principal: Principal,session_id: str,event_id: str,request_id: str,kind: str,payload: dict) -> EventReceipt:
        try:
            event_id=str(UUID(event_id))
            safe=EventPayload.model_validate(payload).model_dump(mode='json',exclude_none=True)
        except (ValueError,ValidationError) as exc:
            raise CommerceError('invalid_event_input') from exc
        if kind not in KINDS or not 1<=len(request_id)<=64:
            raise CommerceError('invalid_event_input')
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


__all__ = [
    'CommerceEventService',
]
