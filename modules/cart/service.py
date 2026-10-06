"""车行只存展示快照；每次读车批量查询现行事实。"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kernel.errors import CommerceError
from kernel.intent import ShoppingIntent
from kernel.quotes import SKUQuote, evaluate_legacy_cart_purchasability
from modules.cart.models import Cart, CartLine, PendingAction
from modules.cart.schemas import (
    ActionProposalCommand,
    CartConfirmationView,
    CartLineSnapshot,
    CartLineView,
    CartSnapshot,
    CartView,
    PendingActionRecord,
    PendingActionView,
)
from modules.catalog.schemas import SaleFactView
from modules.inventory.schemas import AvailabilityView
from modules.pricing.schemas import PriceView
from modules.sale_facts.service import SaleFactsReader
from shared.hashing import JsonObject, request_hash
from shared.ids import new_id
from shared.principal import Principal
from shared.time import mark_utc, utcnow


def _legacy_quote_reason(
    sale: SaleFactView | None,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    """保留锁车报价弱规则；R 批准前不归一币种、负库存与状态差异。"""
    return evaluate_legacy_cart_purchasability(sale, price, availability)


class CartService:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession],
        facts: SaleFactsReader, max_quantity: int = 10, action_ttl_seconds: int = 600,
    ) -> None:
        self.factory,self.facts=factory,facts
        self.max_quantity,self.action_ttl=max_quantity,action_ttl_seconds

    async def ensure_cart(self, db: AsyncSession, principal: Principal) -> Cart:
        stmt=insert(Cart).values(id=new_id(),principal_key=principal.principal_key,version=1)
        await db.execute(stmt.on_duplicate_key_update(id=Cart.id))
        return await db.scalar(select(Cart).where(Cart.principal_key==principal.principal_key))

    async def lock_cart_snapshot(
        self, db: AsyncSession, principal: Principal
    ) -> CartSnapshot:
        cart = await self.ensure_cart(db, principal)
        lines = list(
            await db.scalars(
                select(CartLine)
                .where(
                    CartLine.cart_id == cart.id,
                )
                .order_by(CartLine.sku_id)
            )
        )
        return self._cart_snapshot(cart, lines)

    @staticmethod
    def _cart_snapshot(cart: Cart, lines: list[CartLine]) -> CartSnapshot:
        return CartSnapshot(
            cart_id=cart.id, principal_key=cart.principal_key, version=cart.version,
            lines=[CartLineSnapshot(
                line_id=line.id, sku_id=line.sku_id, quantity=line.quantity,
                product_name_snapshot=line.product_name_snapshot,
                sku_options_snapshot=line.sku_options_snapshot,
                image_url_snapshot=line.image_url_snapshot,
                last_seen_unit_amount_minor=line.last_seen_unit_amount_minor,
                last_seen_price_version=line.last_seen_price_version,
            ) for line in lines],
        ).model_copy(deep=True)

    @staticmethod
    def _action_record(action: PendingAction) -> PendingActionRecord:
        return PendingActionRecord(
            action_id=action.id,
            session_id=action.session_id,
            principal_key=action.principal_key,
            cart_id=action.cart_id,
            kind=action.kind,
            proposal_hash=action.proposal_hash,
            status=action.status,
            source_turn_id=action.source_turn_id,
            confirmation_key=action.confirmation_key,
            confirmation_request_hash=action.confirmation_request_hash,
            expected_cart_version=action.expected_cart_version,
            payload=action.payload_json,
            summary=action.quote_snapshot_json,
            expires_at=action.expires_at,
            created_at=action.created_at,
            result=action.result_json,
        ).model_copy(deep=True)

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
    def action_view(action: PendingAction | PendingActionRecord) -> PendingActionView:
        record = (
            CartService._action_record(action)
            if isinstance(action, PendingAction)
            else action
        )
        return PendingActionView(
            action_id=record.action_id,
            kind=record.kind,
            status=record.status,
            expected_cart_version=record.expected_cart_version,
            payload=record.payload,
            summary=record.summary,
            expires_at=record.expires_at,
        )

    async def lock_quotes(
        self, db: AsyncSession, sku_ids: list[str],
    ) -> tuple[dict[str, SKUQuote], dict[str, SaleFactView]]:
        """固定顺序锁目录→CNY 现价→SKU/库位余额，不持锁调用网络。"""
        return await self.facts.lock_quotes(db, sku_ids)

    @staticmethod
    def quote_snapshot(quote: SKUQuote, sale: SaleFactView | None) -> JsonObject:
        """只比较用户可见事实与价格/规格版本；忽略库存内部版本和读取时间。"""
        return {
            'sku_id': quote.sku_id,
            'product_id': sale.product.id if sale else None,
            'name': sale.product.name if sale else None,
            'sku_options': [
                value.model_dump(mode='json') for value in quote.sku.selected_values
            ]
            if quote.sku
            else [],
            'sku_version': quote.sku.version if quote.sku else None,
            'product_version': sale.product.version if sale else None,
            'reason': quote.reason,
            'currency': quote.price.currency if quote.price else 'CNY',
            'unit_amount_minor': quote.price.amount_minor if quote.price else None,
            'price_version': quote.price.version if quote.price else None,
            'available_quantity': quote.availability.available_quantity
            if quote.availability
            else None,
        }

    @staticmethod
    def validate_target(kind: str, target: dict, max_quantity: int) -> dict:
        fields={'sku_id'} if kind=='remove' else {'sku_id','quantity'}
        if kind not in {'add','set_quantity','remove'} or set(target)!=fields or not isinstance(target.get('sku_id'),str):
            raise CommerceError('invalid_action')
        if kind!='remove' and (type(target['quantity']) is not int or not 1<=target['quantity']<=max_quantity):
            raise CommerceError('invalid_quantity')
        return dict(target)

    async def _find_source_action_row(
        self, db: AsyncSession, principal: Principal, session_id: str,
        source_turn_id: str | None, digest: str,
    ) -> PendingAction | None:
        if source_turn_id is None:
            return None
        existing=await db.scalar(select(PendingAction).where(PendingAction.session_id==session_id,
            PendingAction.principal_key==principal.principal_key,
            PendingAction.source_turn_id==source_turn_id).with_for_update())
        if existing:
            if existing.proposal_hash!=digest:
                raise CommerceError('idempotency_conflict')
            if existing.status!='pending' or existing.expires_at<=utcnow():
                raise CommerceError('action_already_resolved')
        return existing

    async def find_source_action_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        source_turn_id: str | None,
        digest: str,
    ) -> PendingActionRecord | None:
        action = await self._find_source_action_row(
            db, principal, session_id, source_turn_id, digest
        )
        return self._action_record(action) if action is not None else None

    async def _create_pending_action_row(
        self, db: AsyncSession, principal: Principal, session_id: str,
        cart_id: str, cart_version: int, kind: str, target: JsonObject,
        snapshot: JsonObject, digest: str, source_turn_id: str | None = None,
    ) -> PendingAction:
        old=list(await db.scalars(select(PendingAction).where(PendingAction.session_id==session_id,
            PendingAction.status=='pending').order_by(PendingAction.id).with_for_update()))
        for action in old:
            action.status='invalidated'
        await db.flush()
        action=PendingAction(id=new_id(),session_id=session_id,source_turn_id=source_turn_id,
            principal_key=principal.principal_key,cart_id=cart_id,kind=kind,payload_json=target,proposal_hash=digest,
            expected_cart_version=cart_version,quote_snapshot_json=snapshot,status='pending',expires_at=utcnow()+timedelta(seconds=self.action_ttl),created_at=utcnow(),updated_at=utcnow())
        db.add(action);await db.flush()
        return action

    async def create_pending_action_in_transaction(
        self, db: AsyncSession, principal: Principal, session_id: str,
        cart: CartSnapshot, kind: str, target: JsonObject, snapshot: JsonObject,
        digest: str, source_turn_id: str | None = None,
    ) -> PendingActionRecord:
        if cart.principal_key != principal.principal_key:
            raise CommerceError('not_found')
        action = await self._create_pending_action_row(
            db, principal, session_id, cart.cart_id, cart.version, kind,
            target, snapshot, digest, source_turn_id,
        )
        return self._action_record(action)

    async def propose_action_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        command: ActionProposalCommand,
        intent: ShoppingIntent,
    ) -> PendingActionView:
        kind = command.kind
        target = self.validate_target(kind, command.target, self.max_quantity)
        digest = request_hash(
            {
                'kind': kind,
                'target': target,
                'cart_version': command.expected_cart_version,
            }
        )
        cart = await self.ensure_cart(db, principal)
        existing = await self.find_source_action_in_transaction(
            db, principal, session_id, command.source_turn_id, digest
        )
        if existing:
            return self.action_view(existing)
        if cart.version != command.expected_cart_version:
            raise CommerceError('cart_version_conflict', {'version': cart.version})
        sku_id = target['sku_id']
        line = await db.scalar(
            select(CartLine).where(
                CartLine.cart_id == cart.id, CartLine.sku_id == sku_id
            )
        )
        if kind != 'add' and line is None:
            raise CommerceError('not_found')
        quotes, sales = await self.lock_quotes(db, [sku_id])
        quote = quotes[sku_id]
        quantity = target.get('quantity', 0) + (
            line.quantity if kind == 'add' and line else 0
        )
        if kind != 'remove':
            self.assert_buyable(quote, quantity)
            intent = ShoppingIntent.model_validate(intent)
            sale = sales[sku_id]
            definitions = {
                attribute.code: attribute
                for attribute in sale.category_attributes
                if attribute.is_filterable
            }
            attrs = sale.product.attributes or {}
            if (
                (intent.category_id and sale.product.category_id != intent.category_id)
                or (
                    intent.budget_max_minor is not None
                    and quote.price.amount_minor > intent.budget_max_minor
                )
                or any(
                    condition.source != 'inferred'
                    and (
                        condition.code not in definitions
                        or condition.code not in attrs
                        or type(attrs[condition.code]) is not type(condition.value)
                        or attrs[condition.code] != condition.value
                    )
                    for condition in intent.hard_conditions
                )
            ):
                raise CommerceError('constraints_mismatch')
        snapshot = self.quote_snapshot(quote, sales.get(sku_id))
        snapshot['final_quantity'] = quantity
        action = await self.create_pending_action_in_transaction(
            db,
            principal,
            session_id,
            self._cart_snapshot(cart, []),
            kind,
            target,
            snapshot,
            digest,
            command.source_turn_id,
        )
        return self.action_view(action)

    def assert_buyable(self, quote: SKUQuote, quantity: int) -> None:
        if not 1<=quantity<=self.max_quantity:
            raise CommerceError('invalid_quantity')
        if not quote.purchasable:
            raise CommerceError(quote.reason)
        if quote.availability.available_quantity<quantity:
            raise CommerceError('insufficient_stock')

    async def _lock_pending_action_row(
        self, db: AsyncSession, principal: Principal, session_id: str,
        action_id: str, cart_id: str,
    ) -> PendingAction:
        action=await db.scalar(select(PendingAction).where(PendingAction.id==action_id,
            PendingAction.session_id==session_id,PendingAction.principal_key==principal.principal_key,
            PendingAction.cart_id==cart_id).with_for_update())
        if action is None:
            raise CommerceError('not_found')
        return action

    async def lock_pending_action(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        action_id: str,
    ) -> PendingActionRecord:
        cart = await self.ensure_cart(db, principal)
        return self._action_record(
            await self._lock_pending_action_row(
                db, principal, session_id, action_id, cart.id
            )
        )

    async def find_action_owner(
        self, db: AsyncSession, principal: Principal, action_id: str,
    ) -> str:
        session_id=await db.scalar(select(PendingAction.session_id).where(
            PendingAction.id==action_id,PendingAction.principal_key==principal.principal_key))
        if session_id is None:
            raise CommerceError('not_found')
        return session_id

    async def confirmation_replay_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        action: PendingActionRecord,
        approved: bool,
        key: str,
    ) -> JsonObject | None:
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 64
            or type(approved) is not bool
        ):
            raise CommerceError('invalid_confirmation')
        digest = request_hash({'action_id': action.action_id, 'approved': approved})
        used = await db.scalar(
            select(PendingAction)
            .where(
                PendingAction.principal_key == principal.principal_key,
                PendingAction.confirmation_key == key,
            )
            .with_for_update()
        )
        if used:
            used_record = self._action_record(used)
            if (
                used_record.action_id != action.action_id
                or used_record.confirmation_request_hash != digest
            ):
                raise CommerceError('idempotency_conflict')
            return used_record.result
        if action.confirmation_key is not None:
            raise CommerceError('action_already_resolved')
        if action.status != 'pending':
            raise CommerceError('action_already_resolved')
        if action.expires_at <= mark_utc(utcnow()):
            raise CommerceError('action_expired')
        return None

    @staticmethod
    def save_confirmation(action: 'PendingAction', approved: bool, key: str, result: dict) -> None:
        action.confirmation_key=key
        action.confirmation_request_hash=request_hash({'action_id':action.id,'approved':approved})
        action.result_json=result

    async def confirm_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        action_id: str,
        approved: bool,
        idempotency_key: str,
    ) -> CartConfirmationView:
        cart = await self.ensure_cart(db, principal)
        action = await self._lock_pending_action_row(
            db, principal, session_id, action_id, cart.id
        )
        record = self._action_record(action)
        replay = await self.confirmation_replay_in_transaction(
            db, principal, record, approved, idempotency_key
        )
        if replay is not None:
            return CartConfirmationView.model_validate(
                dict(replay, replayed=True)
            ).model_copy(deep=True)
        if record.kind == 'checkout_handoff':
            raise CommerceError('invalid_action')
        if not approved:
            action.status = 'declined'
            result = CartConfirmationView(status='declined')
        else:
            if cart.version != record.expected_cart_version:
                raise CommerceError(
                    'cart_version_conflict', {'version': cart.version}
                )
            sku_id = record.payload['sku_id']
            kind = record.kind
            lines = list(
                await db.scalars(
                    select(CartLine)
                    .where(CartLine.cart_id == cart.id)
                    .order_by(CartLine.sku_id)
                )
            )
            line = next((entry for entry in lines if entry.sku_id == sku_id), None)
            if kind != 'add' and line is None:
                raise CommerceError('not_found')
            quantity = record.payload.get('quantity', 0) + (
                line.quantity if kind == 'add' and line else 0
            )
            quotes, sales = await self.lock_quotes(
                db, sorted({entry.sku_id for entry in lines} | {sku_id})
            )
            quote = quotes[sku_id]
            snapshot = self.quote_snapshot(quote, sales.get(sku_id))
            snapshot['final_quantity'] = quantity
            if kind != 'remove' and snapshot != record.summary:
                action.status = 'invalidated'
                replacement = None
                try:
                    self.assert_buyable(quote, quantity)
                    replacement = self.action_view(
                        await self.create_pending_action_in_transaction(
                            db,
                            principal,
                            session_id,
                            self._cart_snapshot(cart, lines),
                            kind,
                            record.payload,
                            snapshot,
                            record.proposal_hash,
                        )
                    )
                except CommerceError:
                    pass
                result = CartConfirmationView(
                    status='confirm_action',
                    error_code='reconfirmation_required',
                    proposed_action=replacement,
                )
            else:
                if kind != 'remove':
                    self.assert_buyable(quote, quantity)
                    if line is None:
                        line = CartLine(
                            id=new_id(),
                            cart_id=cart.id,
                            sku_id=sku_id,
                            quantity=quantity,
                            product_name_snapshot=snapshot['name'],
                            sku_options_snapshot=snapshot['sku_options'],
                        )
                        db.add(line)
                        lines.append(line)
                    line.quantity = quantity
                    line.product_name_snapshot = snapshot['name']
                    line.sku_options_snapshot = snapshot['sku_options']
                    line.last_seen_unit_amount_minor = quote.price.amount_minor
                    line.last_seen_price_version = quote.price.version
                else:
                    await db.delete(line)
                    lines.remove(line)
                cart.version += 1
                action.status = 'consumed'
                await db.flush()
                result = CartConfirmationView(
                    status='cart_updated',
                    cart=self.cart_view(cart.id, cart.version, lines, quotes),
                )
        self.save_confirmation(
            action, approved, idempotency_key, result.model_dump(mode='json')
        )
        return result.model_copy(deep=True)

    @staticmethod
    def invalidate_action(action: PendingAction) -> None:
        action.status='invalidated'

    def resolve_action(self,action: PendingAction,status: str,approved: bool,key: str,result: dict) -> None:
        """交接服务在共享事务中通过车领域消费动作。"""
        action.status=status
        self.save_confirmation(action,approved,key,result)

    async def resolve_action_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        action_id: str,
        status: str,
        approved: bool,
        key: str,
        result: JsonObject,
    ) -> None:
        cart = await self.ensure_cart(db, principal)
        action = await self._lock_pending_action_row(
            db, principal, session_id, action_id, cart.id
        )
        self.resolve_action(action, status, approved, key, result)

    async def invalidate_action_in_transaction(
        self,
        db: AsyncSession,
        principal: Principal,
        session_id: str,
        action_id: str,
    ) -> None:
        cart = await self.ensure_cart(db, principal)
        action = await self._lock_pending_action_row(
            db, principal, session_id, action_id, cart.id
        )
        self.invalidate_action(action)

    async def get_action(
        self, principal: Principal, session_id: str, action_id: str
    ) -> PendingActionView:
        """图外确认门禁的只读动作查询。"""
        async with self.factory() as db:
            action=await db.scalar(select(PendingAction).where(PendingAction.id==action_id,
                PendingAction.session_id==session_id,PendingAction.principal_key==principal.principal_key))
            if action is None:
                raise CommerceError('not_found')
            return self.action_view(action).model_copy(deep=True)

    async def is_only_pending(self,principal: Principal,session_id: str,action_id: str) -> bool:
        async with self.factory() as db:
            ids=list(await db.scalars(select(PendingAction.id).where(PendingAction.session_id==session_id,
                PendingAction.principal_key==principal.principal_key,PendingAction.status=='pending',PendingAction.expires_at>utcnow())))
            return ids==[action_id]


__all__ = [
    'CartService',
]
