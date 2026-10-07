"""真实 MySQL 下的批量事实读取：查询次数不随商品数增长。"""
import asyncio

from ecom.application.storefront.service import StorefrontService
from ecom.modules.sale_facts.service import SaleFactsReader
from tests.support.commerce_fixtures import seed


def test_real_batch_queries_do_not_grow_with_product_count(migrated_mysql_database):
    from sqlalchemy import event

    async def scenario():
        factory,catalog,prices,stock,cats,products,skus=await seed(migrated_mysql_database)
        queries=[]
        engine=factory.kw['bind'].sync_engine
        def record(conn,cursor,statement,parameters,context,executemany):queries.append(statement)
        event.listen(engine,'before_cursor_execute',record)
        try:
            reader=StorefrontService(catalog,SaleFactsReader(catalog,prices,stock))
            await reader.resolve_products([products[0].id]);one=len(queries);queries.clear()
            result=await reader.resolve_products([p.id for p in products])
            assert len(queries)==one
            assert len(result.items)>0
        finally:event.remove(engine,'before_cursor_execute',record)
    asyncio.run(scenario())
