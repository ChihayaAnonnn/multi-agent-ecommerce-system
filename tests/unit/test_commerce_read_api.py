"""消费者只读接口：报价来源顺序与未配置数据库时的降级。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ecom.api.deps import create_runtime
from ecom.config import Settings
from tests.support.catalog_api_helpers import arequest, build_app, request


def test_single_sku_quote_keeps_independent_source_order():
    from ecom.modules.catalog.schemas import SKUDetailView
    from tests.unit.test_commerce_facts import Inventory, Prices

    calls = []

    class Catalog:
        async def get_sku(self, sku_id):
            calls.append('catalog')
            return SKUDetailView(id=sku_id, product_id='product', sku_code='SKU',
                option_signature='default', status='active',
                product_status='active', version=1)

    class Pricing(Prices):
        async def get_prices(self, ids):
            calls.append('pricing')
            return await super().get_prices(ids)

    class Stock(Inventory):
        async def get_availability(self, ids):
            calls.append('inventory')
            return await super().get_availability(ids)

    response = request(build_app(SimpleNamespace(catalog=Catalog(), pricing=Pricing(),
        inventory=Stock())), 'GET', '/api/v1/commerce/skus/sku-1/quote')
    assert response.status_code == 200 and response.json()['purchasable']
    assert calls == ['catalog', 'inventory', 'pricing']


def test_read_endpoints_return_503_when_database_is_not_configured():
    """未配置数据库时，新只读端点返回 503，并且不泄露连接串。"""
    async def scenario():
        runtime = await create_runtime(Settings(_env_file=None, database_url=""))
        try:
            response = await arequest(
                build_app(runtime), "GET", "/api/v1/catalog/products",
            )
            assert response.status_code == 503
            assert "ECOM_DATABASE_URL" in response.json()["detail"]
            assert "password" not in response.json()["detail"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
