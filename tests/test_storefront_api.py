import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.storefront import router
from tests.test_commerce_facts import Inventory, Prices
from tests.test_storefront import Products


async def scenario():
    app=FastAPI();app.include_router(router)
    app.state.runtime=SimpleNamespace(catalog=Products(),pricing=Prices(),inventory=Inventory(),catalog_error=None)
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        r=await c.get('/api/v1/storefront/products');assert r.status_code==200
        assert r.json()['items'][0]['sku_id']=='unknown'
        assert (await c.get('/api/v1/storefront/products?limit=101')).status_code==422
        assert (await c.post('/api/v1/storefront/products/resolve',json={'product_ids':['0']*101})).status_code==422
        r=await c.post('/api/v1/storefront/products/resolve',json={'product_ids':['0','404']})
        assert r.json()['missing_product_ids']==['404']
        app.state.runtime.catalog=None
        assert (await c.get('/api/v1/storefront/products')).status_code==503

def test_storefront_api_validation_and_dependency_errors():asyncio.run(scenario())
