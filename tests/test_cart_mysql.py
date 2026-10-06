"""主体唯一购物车及实时小计。"""
import asyncio

from sqlalchemy import delete

from modules.cart.models import CartLine
from modules.cart.service import CartService
from modules.identity.types import make_principal
from modules.pricing.models import SKUPrice
from modules.sale_facts.service import SaleFactsReader
from tests.conftest import downgrade_to, read_schema, upgrade_to
from tests.test_commerce_facts_mysql import seed


def test_cart_migration_and_unique_owner(migrated_mysql_database):
    schema = read_schema(migrated_mysql_database)
    assert {'carts','cart_lines','pending_actions'} <= schema['tables']
    assert ('carts',('principal_key',)) in schema['uniques']
    assert ('pending_actions',('active_session_id',)) in schema['uniques']
    downgrade_to(migrated_mysql_database,'0005_commerce_sessions')
    upgrade_to(migrated_mysql_database,'0006_cart')
    async def scenario():
        factory,catalog,prices,stock,*_ = await seed(migrated_mysql_database)
        service = CartService(factory,SaleFactsReader(catalog,prices,stock))
        a = make_principal('test','a'); b = make_principal('test','b')
        results = await asyncio.gather(*(service.get_or_create(a) for _ in range(5)))
        assert len({r.cart_id for r in results}) == 1
        assert (await service.get_cart(a)).cart_id == results[0].cart_id
        assert (await service.get_cart(b)).cart_id != results[0].cart_id
        assert results[0].version == 1 and not results[0].checkout_eligible
    asyncio.run(scenario())


def test_cart_read_uses_current_facts(migrated_mysql_database):
    async def scenario():
        factory,catalog,prices,stock,_,products,skus = await seed(migrated_mysql_database)
        service = CartService(factory,
            SaleFactsReader(catalog, prices, stock))
        a=make_principal('test','a'); cart=await service.get_or_create(a)
        async with factory() as db:
            db.add(CartLine(id='00000000-0000-0000-0000-000000000099',cart_id=cart.cart_id,sku_id=skus[0].id,quantity=2,
                product_name_snapshot='旧名',sku_options_snapshot=[],last_seen_unit_amount_minor=100,last_seen_price_version=1))
            await db.commit()
        assert (await service.get_cart(a)).current_subtotal == 200
        await prices.set_price(skus[0].id,150,1,'改价')
        view = await service.get_cart(a)
        assert view.current_subtotal == 300 and view.items[0].price_changed
        async with factory() as db:
            await db.execute(delete(SKUPrice).where(SKUPrice.sku_id==skus[0].id));await db.commit()
        view = await service.get_cart(a)
        assert view.current_subtotal is None and not view.checkout_eligible
        assert view.items[0].reason == 'price_missing'
    asyncio.run(scenario())
