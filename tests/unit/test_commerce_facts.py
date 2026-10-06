"""报价缺失与依赖故障不能提升为可购买。"""
import asyncio

import pytest

from ecom.application.shopping.facts import CommerceDependencyError, CommerceFactsReader
from ecom.modules.catalog.schemas import SKUDetailView
from ecom.modules.inventory.schemas import AvailabilityView
from ecom.modules.pricing.schemas import PriceView
from ecom.modules.sale_facts.service import SaleFactsReader
from ecom.shared.time import utcnow


class Catalog:
    async def get_skus(self, ids):
        return {i: SKUDetailView(id=i, product_id='p', sku_code=i,
                    option_signature='default', status='active', version=1,
                    product_status='active') for i in ids if i != 'missing'}


class Prices:
    async def get_prices(self, ids):
        return {i: PriceView(sku_id=i, currency='CNY', amount_minor=100,
                    version=1, effective_at=utcnow(), read_at=utcnow())
                for i in ids if i != 'no-price'}


class Inventory:
    async def get_availability(self, ids):
        return {i: AvailabilityView(sku_id=i,
                status='unknown' if i == 'unknown' else 'out_of_stock' if i == 'zero' else 'available',
                available_quantity=None if i == 'unknown' else 0 if i == 'zero' else 3,
                read_at=utcnow()) for i in ids}


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
