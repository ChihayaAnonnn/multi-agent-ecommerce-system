"""车行只存展示快照；每次读车批量查询现行事实。"""
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from cart.models import Cart, CartLine
from cart.schemas import CartView, CartLineView
from commerce_orchestrator.facts import CommerceFactsReader
from shopping_session.service import ShoppingSessionService
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
