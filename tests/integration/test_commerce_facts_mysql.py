"""真实目录批量查询、硬约束扫描与 SQL 次数验收。"""
import asyncio

from sqlalchemy import delete, event, update

from ecom.application.shopping.facts import CommerceFactsReader
from ecom.application.shopping.schemas import HardCondition, ShoppingIntent
from ecom.modules.catalog.models import SKU, Product
from ecom.modules.inventory.models import DEFAULT_LOCATION_ID
from ecom.modules.pricing.models import SKUPrice
from ecom.modules.sale_facts.service import SaleFactsReader
from tests.support.commerce_fixtures import seed


def test_batch_details_have_constant_sql_count(migrated_mysql_database):
    async def scenario():
        factory, catalog, _, _, _, products, skus = await seed(migrated_mysql_database)
        engine = factory.kw['bind'].sync_engine
        calls = []
        def count(*args):
            calls.append(args[2])
        event.listen(engine, 'before_cursor_execute', count)
        try:
            one = await catalog.get_products([products[0].id])
            single_count = len(calls); calls.clear()
            many = await catalog.get_products([p.id for p in products] + ['missing'])
            assert len(calls) == single_count
            assert len(many) == 8
            assert many[products[0].id] == one[products[0].id]
            calls.clear()
            await catalog.get_skus([skus[0].id]); single_count = len(calls); calls.clear()
            details = await catalog.get_skus([s.id for s in skus])
            assert len(calls) == single_count
            assert len(details) == 8
        finally:
            event.remove(engine, 'before_cursor_execute', count)
    asyncio.run(scenario())


def test_scan_hard_filters_and_limit(migrated_mysql_database):
    async def scenario():
        _, catalog, prices, stock, cats, products, skus = await seed(migrated_mysql_database)
        reader = CommerceFactsReader(catalog, SaleFactsReader(catalog, prices, stock))
        result = await reader.scan(ShoppingIntent(category_id=cats[0].id,
            budget_max_minor=350, hard_conditions=[HardCondition(code='wireless', value=True)]), 200, 2)
        assert {q.sku.id for q in result.items} == {skus[0].id, skus[2].id}
        assert not result.incomplete
        limited = await reader.scan(ShoppingIntent(), 3, 2)
        assert limited.scanned == 3 and limited.incomplete
        assert len(limited.items) == 3
        invalid = await reader.scan(ShoppingIntent(hard_conditions=[HardCondition(code='made-up', value=True)]), 200)
        assert not invalid.items
    asyncio.run(scenario())


def test_real_quote_statuses_and_two_categories(migrated_mysql_database):
    async def scenario():
        factory, catalog, prices, stock, _, products, skus = await seed(migrated_mysql_database)
        async with factory() as db:
            await db.execute(update(Product).where(Product.id == products[2].id).values(status='draft'))
            await db.execute(update(Product).where(Product.id == products[3].id).values(status='archived'))
            await db.execute(update(SKU).where(SKU.id == skus[4].id).values(status='inactive'))
            await db.execute(delete(SKUPrice).where(SKUPrice.sku_id == skus[5].id))
            from ecom.modules.inventory.models import InventoryBalance
            await db.execute(delete(InventoryBalance).where(InventoryBalance.sku_id == skus[6].id))
            await db.commit()
        await stock.adjust(skus[7].id, DEFAULT_LOCATION_ID, -5, 'empty', '清空')
        reader = CommerceFactsReader(catalog, SaleFactsReader(catalog, prices, stock))
        q = await reader.quote_many([s.id for s in skus])
        assert [q[s.id].reason for s in skus] == ['purchasable', 'purchasable',
            'product_not_active', 'product_not_active', 'sku_inactive', 'price_missing',
            'stock_unknown', 'out_of_stock']
    asyncio.run(scenario())
