"""本地先提交 preparing，锁外调用宿主，再保存 ready/failed。"""
import asyncio
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker
from checkout.models import CheckoutHandoff
from checkout.schemas import CheckoutPreview,HandoffSnapshot,HandoffResult,Destination
from checkout.gateway import HostCheckoutGateway,validate_destination
from cart.models import CartLine,PendingAction
from cart.service import CartService
from shopping_session.service import ShoppingSessionService,CommerceError
from shopping_session.schemas import TurnClaim
from identity.types import Principal
from db.base import utcnow,new_id
from commerce_orchestrator.utils import request_hash


class CheckoutService:
    def __init__(self,factory: async_sessionmaker[AsyncSession],sessions: ShoppingSessionService,cart: CartService,
        gateway: HostCheckoutGateway,allowed_hosts: tuple[str,...]=(),allowed_path: str='/checkout/',timeout_seconds: float=5.0):
        self.factory,self.sessions,self.cart,self.gateway=factory,sessions,cart,gateway
        self.allowed_hosts,self.allowed_path=allowed_hosts,allowed_path
        self.timeout_seconds=timeout_seconds

    async def cart_snapshot(self,db: AsyncSession,cart) -> tuple[dict,list[dict]]:
        lines=list(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id).order_by(CartLine.sku_id)))
        quotes,sales=await self.cart.lock_quotes(db,[l.sku_id for l in lines])
        items,errors,subtotal=[],[],0
        if not lines:
            errors.append({'code':'empty_cart'})
        for line in lines:
            q=quotes[line.sku_id]
            item=self.cart.quote_snapshot(q,sales.get(line.sku_id))
            item['sku_options']=[v.model_dump(mode='json') for v in item['sku_options']]
            item['quantity']=line.quantity
            items.append(item)
            if q.price is None:
                subtotal=None
            elif subtotal is not None:
                subtotal+=line.quantity*q.price.amount_minor
            try:
                self.cart.assert_buyable(q,line.quantity)
            except CommerceError as exc:
                errors.append({'sku_id':line.sku_id,'code':exc.code})
        return {'items':items,'current_subtotal':subtotal,'currency':'CNY','cart_version':cart.version},errors

    @staticmethod
    def preview_view(action: PendingAction) -> CheckoutPreview:
        return CheckoutPreview(action_id=action.id,checkout_eligible=True,checked_at=action.created_at,
            expires_at=action.expires_at,**action.quote_snapshot_json)

    async def preview(self,principal: Principal,session_id: str,cart_version: int,source_turn: TurnClaim | None=None) -> CheckoutPreview:
        digest=request_hash({'kind':'checkout_handoff','cart_version':cart_version})
        async with self.factory() as db,db.begin():
            await self.sessions.owned_row(db,session_id,principal.principal_key,True)
            if source_turn:
                await self.sessions.assert_current_turn(db,source_turn)
            cart=await self.cart.ensure_cart(db,principal)
            existing=await self.cart.source_action(db,principal,session_id,source_turn,digest)
            if existing:
                return self.preview_view(existing)
            if cart.version!=cart_version:
                raise CommerceError('cart_version_conflict',409,{'version':cart.version})
            snapshot,errors=await self.cart_snapshot(db,cart)
            if errors:
                return CheckoutPreview(checked_at=utcnow(),errors=errors,**snapshot)
            action=await self.cart.new_action(db,principal,session_id,cart,'checkout_handoff',
                {'cart_version':cart_version},snapshot,digest,source_turn)
            return self.preview_view(action)

    @staticmethod
    def result(row: CheckoutHandoff,replayed: bool=False) -> HandoffResult:
        if row.expires_at<=utcnow():
            return HandoffResult(status='unavailable',handoff_id=row.id,error_code='handoff_expired')
        return HandoffResult(status='handoff' if row.status=='ready' else 'unavailable',handoff_id=row.id,
            destination=Destination(kind=row.destination_kind,ref=row.destination_ref,demo=row.destination_demo) if row.status=='ready' else None,
            expires_at=row.expires_at,error_code=None if row.status=='ready' else 'checkout_unavailable',
            retryable=row.status in {'preparing','failed'},replayed=replayed)

    async def get_owned(self,principal: Principal,session_id: str,handoff_id: str) -> HandoffResult:
        async with self.factory() as db:
            await self.sessions.owned_row(db,session_id,principal.principal_key)
            row=await db.scalar(select(CheckoutHandoff).join(PendingAction,PendingAction.id==CheckoutHandoff.action_id).where(
                CheckoutHandoff.id==handoff_id,CheckoutHandoff.principal_key==principal.principal_key,PendingAction.session_id==session_id))
            if row is None:
                raise CommerceError('not_found',404)
            return self.result(row)

    async def confirm_handoff(self,principal: Principal,session_id: str,action_id: str,approved: bool,idempotency_key: str) -> HandoffResult:
        async with self.factory() as db,db.begin():
            cart,action=await self.cart.locked_action(db,principal,session_id,action_id)
            if action.kind!='checkout_handoff':
                raise CommerceError('invalid_action',422)
            replay=await self.cart.confirmation_replay(db,principal,action,approved,idempotency_key)
            row=await db.scalar(select(CheckoutHandoff).where(CheckoutHandoff.action_id==action.id).with_for_update())
            if replay is not None and row is None:
                return HandoffResult.model_validate(dict(replay,replayed=True))
            if row:
                if row.status=='ready' or row.expires_at<=utcnow():
                    return self.result(row,True)
            elif not approved:
                result=HandoffResult(status='declined')
                self.cart.resolve_action(action,'declined',approved,idempotency_key,result.model_dump(mode='json'))
                return result
            else:
                if not self.gateway.available:
                    raise CommerceError('checkout_unavailable',503)
                if cart.version!=action.expected_cart_version:
                    raise CommerceError('cart_version_conflict',409,{'version':cart.version})
                snapshot,errors=await self.cart_snapshot(db,cart)
                if errors or snapshot!=action.quote_snapshot_json:
                    self.cart.invalidate_action(action)
                    replacement=None
                    if not errors:
                        replacement=self.preview_view(await self.cart.new_action(db,principal,session_id,cart,'checkout_handoff',
                            {'cart_version':cart.version},snapshot,request_hash({'kind':'checkout_handoff','cart_version':cart.version})))
                    result=HandoffResult(status='confirm_action',error_code='reconfirmation_required',preview=replacement)
                    self.cart.save_confirmation(action,approved,idempotency_key,result.model_dump(mode='json'))
                    return result
                handoff_id=new_id();expiry=utcnow()+timedelta(seconds=self.cart.action_ttl)
                immutable=HandoffSnapshot(handoff_id=handoff_id,principal_key=principal.principal_key,issuer=principal.issuer,
                    subject=principal.subject,cart_id=cart.id,expires_at=expiry,**snapshot)
                payload=immutable.model_dump(mode='json')
                row=CheckoutHandoff(id=handoff_id,principal_key=principal.principal_key,cart_id=cart.id,cart_version=cart.version,
                    action_id=action.id,snapshot_json=payload,snapshot_hash=request_hash(payload),status='preparing',expires_at=expiry,destination_demo=False)
                db.add(row)
                self.cart.resolve_action(action,'consumed',approved,idempotency_key,HandoffResult(status='unavailable',handoff_id=handoff_id,retryable=True).model_dump(mode='json'))
            snapshot_model=HandoffSnapshot.model_validate(row.snapshot_json)
            handoff_id=row.id
        # 宿主网络永远在数据库事务外；并发重试使用相同宿主幂等键。
        try:
            destination=await asyncio.wait_for(self.gateway.create_handoff(snapshot_model,handoff_id),self.timeout_seconds)
            destination=validate_destination(Destination.model_validate(destination),self.allowed_hosts,self.allowed_path)
        except Exception:
            destination=None
        async with self.factory() as db,db.begin():
            row=await db.scalar(select(CheckoutHandoff).where(CheckoutHandoff.id==handoff_id,
                CheckoutHandoff.principal_key==principal.principal_key).with_for_update())
            if row.status!='ready':
                if destination is None:
                    row.status='failed'
                elif row.expires_at>utcnow():
                    row.status='ready';row.destination_kind=destination.kind;row.destination_ref=destination.ref;row.destination_demo=destination.demo
                else:
                    row.status='expired'
            return self.result(row,replay is not None)
