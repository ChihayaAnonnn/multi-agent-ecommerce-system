"""真实目录批量查询、硬约束扫描与 SQL 次数验收。"""
import asyncio
from sqlalchemy import event, update, delete
from catalog.models import Product, SKU
from catalog.schemas import CategoryInput, AttributeInput, ProductInput, SKUInput
from catalog.service import CatalogService
from db.session import create_session_factory
from pricing.models import SKUPrice
from pricing.service import PricingService
from inventory.service import InventoryService
from inventory.models import DEFAULT_LOCATION_ID
from commerce_orchestrator.facts import CommerceFactsReader
from commerce_orchestrator.schemas import ShoppingIntent, HardCondition


async def seed(url):
    factory = create_session_factory(url)
    prices, stock = PricingService(factory), InventoryService(factory)
    catalog = CatalogService(factory, prices)
    cats = []
    for code in ['phone', 'headphone']:
        c = await catalog.create_category(CategoryInput(code=code, name=code))
        await catalog.add_category_attribute(c.id, AttributeInput(code='wireless',
            name='无线', value_type='boolean', is_filterable=True, is_comparable=True))
        cats.append(c)
    products, skus = [], []
    for n in range(8):
        p = await catalog.create_product(ProductInput(category_id=cats[n % 2].id,
            product_code=f'p{n}', name=f'商品{n}', attributes={'wireless': n < 4}))
        s = await catalog.create_sku(p.id, SKUInput(sku_code=f's{n}'))
        await prices.set_price(s.id, 100 + n * 100, None, '初始化')
        await stock.create_balance(s.id)
        await stock.adjust(s.id, DEFAULT_LOCATION_ID, 5, f'stock{n}', '初始化')
        await catalog.activate_product(p.id, p.version)
        products.append(p); skus.append(s)
    return factory, catalog, prices, stock, cats, products, skus


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
        reader = CommerceFactsReader(catalog, prices, stock)
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
            from inventory.models import InventoryBalance
            await db.execute(delete(InventoryBalance).where(InventoryBalance.sku_id == skus[6].id))
            await db.commit()
        await stock.adjust(skus[7].id, DEFAULT_LOCATION_ID, -5, 'empty', '清空')
        q = await CommerceFactsReader(catalog, prices, stock).quote_many([s.id for s in skus])
        assert [q[s.id].reason for s in skus] == ['purchasable', 'purchasable',
            'product_not_active', 'product_not_active', 'sku_inactive', 'price_missing',
            'stock_unknown', 'out_of_stock']
    asyncio.run(scenario())
