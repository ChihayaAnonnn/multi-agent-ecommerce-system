"""锁定读取必须在调用方事务内看到未提交的改价（真实 MySQL）。"""

import asyncio

from ecom.modules.catalog.schemas import SaleFactView
from tests.support.commerce_fixtures import seed


def test_locked_reader_keeps_real_mysql_transaction(migrated_mysql_database):
    from sqlalchemy import update

    from ecom.modules.pricing.models import SKUPrice
    from ecom.modules.sale_facts.service import SaleFactsReader

    async def scenario():
        seeded = await seed(migrated_mysql_database)
        factory, catalog, pricing, inventory, _, _, skus = seeded
        sku_id = skus[0].id
        original = (await pricing.get_prices([sku_id]))[sku_id].amount_minor
        reader = SaleFactsReader(catalog, pricing, inventory)
        async with factory() as db:
            await db.begin()
            await db.execute(update(SKUPrice).where(SKUPrice.sku_id == sku_id)
                .values(amount_minor=original + 17))
            quotes, sales = await reader.lock_quotes(db, [sku_id])
            assert db.in_transaction()
            assert quotes[sku_id].price.amount_minor == original + 17
            assert isinstance(sales[sku_id], SaleFactView)
            await db.rollback()
        restored = (await pricing.get_prices([sku_id]))[sku_id]
        assert restored.amount_minor == original

    asyncio.run(scenario())
