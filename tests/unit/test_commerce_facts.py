"""报价缺失与依赖故障不能提升为可购买。"""
import asyncio

import pytest

from ecom.application.shopping.facts import CommerceDependencyError, CommerceFactsReader
from ecom.modules.sale_facts.service import SaleFactsReader
from tests.support.commerce_stubs import Catalog, Inventory, Prices


def test_quote_many_never_promotes_unknown_to_purchasable():
    async def scenario():
        catalog = Catalog()
        reader = CommerceFactsReader(catalog,
            SaleFactsReader(catalog, Prices(), Inventory()))
        q = await reader.quote_many(
            ['ok', 'unknown', 'zero', 'no-price', 'missing'])
        assert q['ok'].purchasable
        assert {k: v.reason for k, v in q.items() if k != 'ok'} == {
            'unknown': 'stock_unknown', 'zero': 'out_of_stock',
            'no-price': 'price_missing', 'missing': 'sku_not_found'}
    asyncio.run(scenario())


@pytest.mark.parametrize('dependency', ['catalog', 'pricing', 'inventory'])
def test_quote_dependency_failure_is_not_empty(dependency):
    from sqlalchemy.exc import OperationalError
    class Broken:
        async def get_skus(self, ids):
            raise OperationalError('query', {}, Exception('offline'))
        get_prices = get_skus
        get_availability = get_skus
    deps = dict(catalog=Catalog(), pricing=Prices(), inventory=Inventory())
    deps[dependency] = Broken()
    with pytest.raises(CommerceDependencyError):
        reader = CommerceFactsReader(deps['catalog'], SaleFactsReader(**deps))
        asyncio.run(reader.quote_many(['ok']))
