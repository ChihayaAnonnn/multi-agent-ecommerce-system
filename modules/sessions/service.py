"""短事务声明轮次；模型和外部事实查询在锁外运行。"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kernel.errors import CommerceError as CommerceError
from modules.sessions.models import CommerceSession, CommerceTurn, SeenProduct, SeenSKU
from modules.sessions.schemas import (
    ShoppingSessionView,
    TurnClaim,
    TurnCompletion,
    TurnReplay,
)
from shared.ids import new_id
from shared.principal import Principal
from shared.time import utcnow


class ShoppingSessionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], ttl_seconds: int = 86400, lease_seconds: int = 120):
        self.factory, self.ttl, self.lease = session_factory, ttl_seconds, lease_seconds

    @staticmethod
    def view(row: CommerceSession) -> ShoppingSessionView:
        return ShoppingSessionView(session_id=row.id, expires_at=row.expires_at, version=row.version, state=row.state_json)

    async def owned_row(self, db: AsyncSession, session_id: str, principal_key: str, lock: bool = False) -> CommerceSession:
        stmt = select(CommerceSession).where(CommerceSession.id == session_id, CommerceSession.principal_key == principal_key)
        if lock:
            stmt = stmt.with_for_update()
        row = await db.scalar(stmt)
        if row is None:
            raise CommerceError('not_found')
        if row.status != 'active' or row.expires_at <= utcnow():
            raise CommerceError('session_expired')
        return row

    async def create(self, principal: Principal) -> ShoppingSessionView:
        async with self.factory() as db, db.begin():
            row = CommerceSession(id=new_id(), principal_key=principal.principal_key,
                expires_at=utcnow()+timedelta(seconds=self.ttl), state_json={}, version=1)
            db.add(row); await db.flush()
            return self.view(row)

    async def lock_owned_session(
        self, db: AsyncSession, session_id: str, principal_key: str,
    ) -> ShoppingSessionView:
        row = await self.owned_row(db, session_id, principal_key, True)
        return self.view(row).model_copy(deep=True)

    async def has_seen_sku(
        self, db: AsyncSession, session_id: str, sku_id: str,
    ) -> bool:
        return await db.get(SeenSKU, (session_id, sku_id)) is not None

    async def get_owned(self, session_id: str, principal: Principal) -> ShoppingSessionView:
        async with self.factory() as db:
            return self.view(await self.owned_row(db, session_id, principal.principal_key))

    async def _record_seen(self, db: AsyncSession, session_id: str, product_ids: list[str], sku_ids: list[str]) -> None:
        for model, field, ids in [(SeenProduct, 'product_id', product_ids), (SeenSKU, 'sku_id', sku_ids)]:
            if ids:
                stmt = insert(model).values([{ 'session_id':session_id, field:i, 'first_seen_at':utcnow()} for i in set(ids)])
                await db.execute(stmt.on_duplicate_key_update(first_seen_at=model.first_seen_at))

    async def record_seen(self, session_id: str, principal: Principal, product_ids: list[str], sku_ids: list[str]) -> None:
        async with self.factory() as db, db.begin():
            await self.owned_row(db, session_id, principal.principal_key, True)
            await self._record_seen(db, session_id, product_ids, sku_ids)

    async def seen(self, session_id: str, principal: Principal) -> tuple[set[str], set[str]]:
        async with self.factory() as db:
            await self.owned_row(db, session_id, principal.principal_key)
            return (set(await db.scalars(select(SeenProduct.product_id).where(SeenProduct.session_id == session_id))),
                set(await db.scalars(select(SeenSKU.sku_id).where(SeenSKU.session_id == session_id))))

    async def claim_turn(self, session_id: str, principal: Principal, turn_id: str, request_hash: str) -> TurnClaim | TurnReplay:
        async with self.factory() as db, db.begin():
            row = await self.owned_row(db, session_id, principal.principal_key, True)
            turn = await db.get(CommerceTurn, (session_id, turn_id))
            if turn is not None:
                if turn.request_hash != request_hash:
                    raise CommerceError('idempotency_conflict')
                if turn.status == 'completed':
                    replay = TurnReplay(
                        session_id=session_id, turn_id=turn_id,
                        response=turn.response_json,
                    )
                    if (
                        replay.response.get('session_id') != session_id
                        or replay.response.get('turn_id') != turn_id
                    ):
                        raise CommerceError('invalid_completion')
                    return replay
            now = utcnow()
            if row.active_turn_id is not None and row.turn_lease_expires_at is not None and row.turn_lease_expires_at > now:
                raise CommerceError('turn_processing', {'retryable':True})
            token = new_id()
            row.active_turn_id, row.turn_lease_token = turn_id, token
            row.turn_lease_expires_at = now + timedelta(seconds=self.lease)
            if turn is None:
                db.add(CommerceTurn(session_id=session_id, turn_id=turn_id, request_hash=request_hash,
                    status='processing', created_at=now))
            return TurnClaim(session_id=session_id, principal_key=principal.principal_key, turn_id=turn_id,
                lease_token=token, session_version=row.version, state=row.state_json)

    async def assert_current_turn(self, db: AsyncSession, claim: TurnClaim) -> None:
        row = await self.owned_row(db, claim.session_id, claim.principal_key, True)
        if row.active_turn_id != claim.turn_id or row.turn_lease_token != claim.lease_token or row.version != claim.session_version or row.turn_lease_expires_at is None or row.turn_lease_expires_at <= utcnow():
            raise CommerceError('turn_lease_lost')

    async def complete_turn(self, claim: TurnClaim, completion: TurnCompletion) -> None:
        completion = TurnCompletion.model_validate(
            dict(completion)
        )
        if not (
            completion.session_id
            == completion.response.get('session_id')
            == claim.session_id
            and completion.turn_id
            == completion.response.get('turn_id')
            == claim.turn_id
        ):
            raise CommerceError('invalid_completion')
        async with self.factory() as db, db.begin():
            await self.assert_current_turn(db, claim)
            row = await db.get(CommerceSession, claim.session_id)
            turn = await db.get(CommerceTurn, (claim.session_id, claim.turn_id))
            turn.status, turn.response_json = 'completed', completion.response
            row.state_json, row.version = completion.state, row.version+1
            row.active_turn_id = row.turn_lease_token = row.turn_lease_expires_at = None
            await self._record_seen(db, row.id, completion.product_ids, completion.sku_ids)

    async def release_turn(self,claim: TurnClaim) -> None:
        """失败的执行者仅释放自己仍持有的租约，绝不清理后继轮次。"""
        async with self.factory() as db,db.begin():
            row=await db.scalar(select(CommerceSession).where(CommerceSession.id==claim.session_id,
                CommerceSession.principal_key==claim.principal_key).with_for_update())
            if row and row.active_turn_id==claim.turn_id and row.turn_lease_token==claim.lease_token:
                row.active_turn_id=row.turn_lease_token=row.turn_lease_expires_at=None


__all__ = [
    'ShoppingSessionService',
]
