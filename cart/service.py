"""车行只存展示快照；每次读车批量查询现行事实。"""
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from cart.models import Cart, CartLine, PendingAction
from cart.schemas import CartView, CartLineView, PendingActionView, CartConfirmationView
from commerce_orchestrator.facts import CommerceFactsReader, CommerceDependencyError
from catalog.service import CatalogError
from pricing.service import PricingDependencyError
from inventory.service import InventoryDependencyError
from sqlalchemy.exc import SQLAlchemyError
from shopping_session.service import ShoppingSessionService, CommerceError
from shopping_session.models import SeenSKU
from shopping_session.schemas import TurnClaim
from commerce_orchestrator.schemas import SKUQuote, ShoppingIntent
from commerce_orchestrator.utils import request_hash
from identity.types import Principal
from db.base import utcnow, new_id


class CartService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], sessions: ShoppingSessionService, facts: CommerceFactsReader, max_quantity: int = 10, action_ttl_seconds: int = 600):
        self.factory,self.sessions,self.facts=factory,sessions,facts
        self.max_quantity,self.action_ttl=max_quantity,action_ttl_seconds

    async def ensure_cart(self, db: AsyncSession, principal: Principal) -> Cart:
        stmt=insert(Cart).values(id=new_id(),principal_key=principal.principal_key,version=1)
        await db.execute(stmt.on_duplicate_key_update(id=Cart.id))
        return await db.scalar(select(Cart).where(Cart.principal_key==principal.principal_key))

    async def get_or_create(self, principal: Principal) -> CartView:
        async with self.factory() as db, db.begin():
            await self.ensure_cart(db,principal)
        return await self.get_cart(principal)

    async def get_cart(self, principal: Principal) -> CartView:
        async with self.factory() as db, db.begin():
            cart=await self.ensure_cart(db,principal)
            lines=list(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id).order_by(CartLine.sku_id)))
            cart_id,version=cart.id,cart.version
        quotes=await self.facts.quote_many([line.sku_id for line in lines])
        return self.cart_view(cart_id,version,lines,quotes)

    @staticmethod
    def cart_view(cart_id: str, version: int, lines: list[CartLine], quotes: dict) -> CartView:
        items=[]; subtotal=0; eligible=bool(lines)
        for line in lines:
            quote=quotes[line.sku_id]
            reason=quote.reason
            if quote.purchasable and quote.availability.available_quantity < line.quantity:
                reason='insufficient_stock'
            eligible=eligible and reason=='purchasable'
            if quote.price is None:
                subtotal=None
            elif subtotal is not None:
                subtotal+=quote.price.amount_minor*line.quantity
            items.append(CartLineView(line_id=line.id,sku_id=line.sku_id,quantity=line.quantity,
                product_name=line.product_name_snapshot,sku_options=line.sku_options_snapshot,quote=quote,
                reason=reason,price_changed=quote.price is not None and quote.price.amount_minor!=line.last_seen_unit_amount_minor))
        return CartView(cart_id=cart_id,version=version,items=items,current_subtotal=subtotal,checkout_eligible=eligible,checked_at=utcnow())

    @staticmethod
    def action_view(action: 'PendingAction') -> 'PendingActionView':
        return PendingActionView(action_id=action.id,kind=action.kind,status=action.status,
            expected_cart_version=action.expected_cart_version,payload=action.payload_json,
            summary=action.quote_snapshot_json,expires_at=action.expires_at)

    async def lock_quotes(self, db: AsyncSession, sku_ids: list[str]) -> tuple[dict, dict]:
        """固定顺序锁目录→CNY 现价→SKU/库位余额，不持锁调用网络。"""
        try:
            sales=await self.facts.catalog.lock_sale_facts(db,sku_ids)
            prices=await self.facts.pricing.lock_prices(db,sku_ids)
            stock=await self.facts.inventory.lock_availability(db,sku_ids)
        except (SQLAlchemyError,CatalogError,PricingDependencyError,InventoryDependencyError) as exc:
            raise CommerceDependencyError('可信交易事实暂不可用') from exc
        quotes={}
        for sku_id in sku_ids:
            sale,price,availability=sales.get(sku_id),prices.get(sku_id),stock.get(sku_id)
            reason=('sku_not_found' if sale is None else 'sku_inactive' if sale.sku.status!='active'
                else 'product_not_active' if sale.product.status!='active' else 'price_missing' if price is None
                else 'stock_unknown' if availability is None or availability.status=='unknown'
                else 'out_of_stock' if not availability.available_quantity else 'purchasable')
            quotes[sku_id]=SKUQuote(sku_id=sku_id,sku=sale.sku if sale else None,price=price,
                availability=availability,purchasable=reason=='purchasable',reason=reason,read_at=utcnow())
        return quotes,sales

    @staticmethod
    def quote_snapshot(quote: SKUQuote, sale) -> dict:
        """只比较用户可见事实与价格/规格版本；忽略库存内部版本和读取时间。"""
        return {'sku_id':quote.sku_id,'product_id':sale.product.id if sale else None,
            'name':sale.product.name if sale else None,'sku_options':quote.sku.selected_values if quote.sku else [],
            'sku_version':quote.sku.version if quote.sku else None,
            'product_version':sale.product.version if sale else None,
            'reason':quote.reason,'currency':quote.price.currency if quote.price else 'CNY',
            'unit_amount_minor':quote.price.amount_minor if quote.price else None,
            'price_version':quote.price.version if quote.price else None,
            'available_quantity':quote.availability.available_quantity if quote.availability else None}

    @staticmethod
    def validate_target(kind: str, target: dict, max_quantity: int) -> dict:
        fields={'sku_id'} if kind=='remove' else {'sku_id','quantity'}
        if kind not in {'add','set_quantity','remove'} or set(target)!=fields or not isinstance(target.get('sku_id'),str):
            raise CommerceError('invalid_action',422)
        if kind!='remove' and (type(target['quantity']) is not int or not 1<=target['quantity']<=max_quantity):
            raise CommerceError('invalid_quantity',422)
        return dict(target)

    async def source_action(self, db: AsyncSession, principal: Principal, session_id: str, source_turn: 'TurnClaim | None', digest: str):
        if source_turn is None:
            return None
        if source_turn.session_id!=session_id or source_turn.principal_key!=principal.principal_key:
            raise CommerceError('turn_lease_lost')
        await self.sessions.assert_current_turn(db,source_turn)
        existing=await db.scalar(select(PendingAction).where(PendingAction.session_id==session_id,
            PendingAction.source_turn_id==source_turn.turn_id).with_for_update())
        if existing:
            if existing.proposal_hash!=digest:
                raise CommerceError('idempotency_conflict')
            if existing.status!='pending' or existing.expires_at<=utcnow():
                raise CommerceError('action_already_resolved')
        return existing

    async def new_action(self, db: AsyncSession, principal: Principal, session_id: str, cart: Cart,
        kind: str, target: dict, snapshot: dict, digest: str, source_turn: 'TurnClaim | None' = None) -> 'PendingAction':
        old=list(await db.scalars(select(PendingAction).where(PendingAction.session_id==session_id,
            PendingAction.status=='pending').order_by(PendingAction.id).with_for_update()))
        for action in old:
            action.status='invalidated'
        await db.flush()
        action=PendingAction(id=new_id(),session_id=session_id,source_turn_id=source_turn.turn_id if source_turn else None,
            principal_key=principal.principal_key,cart_id=cart.id,kind=kind,payload_json=target,proposal_hash=digest,
            expected_cart_version=cart.version,quote_snapshot_json=snapshot,status='pending',expires_at=utcnow()+timedelta(seconds=self.action_ttl),created_at=utcnow(),updated_at=utcnow())
        db.add(action);await db.flush()
        return action

    async def propose_action(self, principal: Principal, session_id: str, kind: str, target: dict,
        expected_cart_version: int, source_turn: 'TurnClaim | None' = None) -> 'PendingActionView':
        target=self.validate_target(kind,target,self.max_quantity)
        digest=request_hash({'kind':kind,'target':target,'cart_version':expected_cart_version})
        async with self.factory() as db, db.begin():
            session_row=await self.sessions.owned_row(db,session_id,principal.principal_key,True)
            if source_turn:
                await self.sessions.assert_current_turn(db,source_turn)
            cart=await self.ensure_cart(db,principal)
            existing=await self.source_action(db,principal,session_id,source_turn,digest)
            if existing:
                return self.action_view(existing)
            if cart.version!=expected_cart_version:
                raise CommerceError('cart_version_conflict',409,{'version':cart.version})
            sku_id=target['sku_id']
            line=await db.scalar(select(CartLine).where(CartLine.cart_id==cart.id,CartLine.sku_id==sku_id))
            if kind=='add':
                seen=await db.get(SeenSKU,(session_id,sku_id))
                if seen is None:
                    raise CommerceError('not_seen',422)
            elif line is None:
                raise CommerceError('not_found',404)
            quotes,sales=await self.lock_quotes(db,[sku_id])
            q=quotes[sku_id]
            quantity=target.get('quantity',0)+(line.quantity if kind=='add' and line else 0)
            if kind!='remove':
                self.assert_buyable(q,quantity)
                intent=ShoppingIntent.model_validate(source_turn.state if source_turn else session_row.state_json)
                sale=sales[sku_id]
                definitions={a.code:a for a in sale.category_attributes if a.is_filterable}
                attrs=sale.product.attributes or {}
                if (intent.category_id and sale.product.category_id!=intent.category_id) or (
                    intent.budget_max_minor is not None and q.price.amount_minor>intent.budget_max_minor) or any(
                    c.source!='inferred' and (c.code not in definitions or c.code not in attrs or type(attrs[c.code]) is not type(c.value) or attrs[c.code]!=c.value)
                    for c in intent.hard_conditions):
                    raise CommerceError('constraints_mismatch',422)
            snapshot=self.quote_snapshot(q,sales.get(sku_id))
            snapshot['sku_options']=[v.model_dump(mode='json') for v in snapshot['sku_options']]
            snapshot['final_quantity']=quantity
            action=await self.new_action(db,principal,session_id,cart,kind,target,snapshot,digest,source_turn)
            return self.action_view(action)

    def assert_buyable(self, quote: SKUQuote, quantity: int) -> None:
        if not 1<=quantity<=self.max_quantity:
            raise CommerceError('invalid_quantity',422)
        if not quote.purchasable:
            raise CommerceError(quote.reason,422)
        if quote.availability.available_quantity<quantity:
            raise CommerceError('insufficient_stock',422)

    async def locked_action(self, db: AsyncSession, principal: Principal, session_id: str, action_id: str) -> tuple[Cart, 'PendingAction']:
        await self.sessions.owned_row(db,session_id,principal.principal_key)
        cart=await self.ensure_cart(db,principal)
        action=await db.scalar(select(PendingAction).where(PendingAction.id==action_id,
            PendingAction.session_id==session_id,PendingAction.principal_key==principal.principal_key,
            PendingAction.cart_id==cart.id).with_for_update())
        if action is None:
            raise CommerceError('not_found',404)
        return cart,action

    async def confirmation_replay(self, db: AsyncSession, principal: Principal, action: 'PendingAction', approved: bool, key: str) -> dict | None:
        if not isinstance(key,str) or not 1<=len(key)<=64 or type(approved) is not bool:
            raise CommerceError('invalid_confirmation',422)
        digest=request_hash({'action_id':action.id,'approved':approved})
        used=await db.scalar(select(PendingAction).where(PendingAction.principal_key==principal.principal_key,
            PendingAction.confirmation_key==key).with_for_update())
        if used:
            if used.id!=action.id or used.confirmation_request_hash!=digest:
                raise CommerceError('idempotency_conflict')
            return used.result_json
        if action.confirmation_key is not None:
            raise CommerceError('action_already_resolved')
        if action.status!='pending':
            raise CommerceError('action_already_resolved')
        if action.expires_at<=utcnow():
            raise CommerceError('action_expired')
        return None

    @staticmethod
    def save_confirmation(action: 'PendingAction', approved: bool, key: str, result: dict) -> None:
        action.confirmation_key=key
        action.confirmation_request_hash=request_hash({'action_id':action.id,'approved':approved})
        action.result_json=result

    async def confirm(self, principal: Principal, session_id: str, action_id: str, approved: bool, idempotency_key: str,source_turn: TurnClaim | None=None) -> 'CartConfirmationView':
        async with self.factory() as db, db.begin():
            if source_turn is not None:
                if source_turn.session_id!=session_id or source_turn.principal_key!=principal.principal_key:
                    raise CommerceError('turn_lease_lost',409)
                await self.sessions.assert_current_turn(db,source_turn)
            cart,action=await self.locked_action(db,principal,session_id,action_id)
            replay=await self.confirmation_replay(db,principal,action,approved,idempotency_key)
            if replay is not None:
                return CartConfirmationView.model_validate(dict(replay,replayed=True))
            if action.kind=='checkout_handoff':
                raise CommerceError('invalid_action',422)
            if not approved:
                action.status='declined'; result=CartConfirmationView(status='declined')
            else:
                if cart.version!=action.expected_cart_version:
                    raise CommerceError('cart_version_conflict',409,{'version':cart.version})
                sku_id=action.payload_json['sku_id'];kind=action.kind
                lines=list(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id).order_by(CartLine.sku_id)))
                line=next((l for l in lines if l.sku_id==sku_id),None)
                if kind!='add' and line is None:
                    raise CommerceError('not_found',404)
                quantity=action.payload_json.get('quantity',0)+(line.quantity if kind=='add' and line else 0)
                quotes,sales=await self.lock_quotes(db,sorted({l.sku_id for l in lines}|{sku_id}))
                q=quotes[sku_id];snapshot=self.quote_snapshot(q,sales.get(sku_id))
                snapshot['sku_options']=[v.model_dump(mode='json') for v in snapshot['sku_options']]
                snapshot['final_quantity']=quantity
                if kind!='remove' and snapshot!=action.quote_snapshot_json:
                    action.status='invalidated'
                    replacement=None
                    try:
                        self.assert_buyable(q,quantity)
                        replacement=self.action_view(await self.new_action(db,principal,session_id,cart,kind,action.payload_json,snapshot,action.proposal_hash))
                    except CommerceError:
                        pass
                    result=CartConfirmationView(status='confirm_action',error_code='reconfirmation_required',proposed_action=replacement)
                else:
                    if kind!='remove':
                        self.assert_buyable(q,quantity)
                        if line is None:
                            line=CartLine(id=new_id(),cart_id=cart.id,sku_id=sku_id,quantity=quantity,
                                product_name_snapshot=snapshot['name'],sku_options_snapshot=snapshot['sku_options'])
                            db.add(line);lines.append(line)
                        line.quantity=quantity
                        line.product_name_snapshot=snapshot['name'];line.sku_options_snapshot=snapshot['sku_options']
                        line.last_seen_unit_amount_minor=q.price.amount_minor;line.last_seen_price_version=q.price.version
                    else:
                        await db.delete(line);lines.remove(line)
                    cart.version+=1;action.status='consumed'
                    await db.flush()
                    result=CartConfirmationView(status='cart_updated',cart=self.cart_view(cart.id,cart.version,lines,quotes))
            self.save_confirmation(action,approved,idempotency_key,result.model_dump(mode='json'))
            return result

    @staticmethod
    def invalidate_action(action: PendingAction) -> None:
        action.status='invalidated'

    def resolve_action(self,action: PendingAction,status: str,approved: bool,key: str,result: dict) -> None:
        """交接服务在共享事务中通过车领域消费动作。"""
        action.status=status
        self.save_confirmation(action,approved,key,result)

    async def get_action(self,principal: Principal,session_id: str,action_id: str) -> PendingAction:
        """图外确认门禁的只读动作查询。"""
        async with self.factory() as db:
            await self.sessions.owned_row(db,session_id,principal.principal_key)
            action=await db.scalar(select(PendingAction).where(PendingAction.id==action_id,
                PendingAction.session_id==session_id,PendingAction.principal_key==principal.principal_key))
            if action is None:
                raise CommerceError('not_found',404)
            return action

    async def is_only_pending(self,principal: Principal,session_id: str,action_id: str) -> bool:
        async with self.factory() as db:
            await self.sessions.owned_row(db,session_id,principal.principal_key)
            ids=list(await db.scalars(select(PendingAction.id).where(PendingAction.session_id==session_id,
                PendingAction.principal_key==principal.principal_key,PendingAction.status=='pending',PendingAction.expires_at>utcnow())))
            return ids==[action_id]
