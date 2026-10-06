"""展厅商品引用需核验主体、会话、商品与 SKU；不接受自报事实。"""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.auth import require_principal
from ecom.api.routes.commerce import router
from ecom.modules.identity.types import make_principal
from ecom.modules.sessions.service import ShoppingSessionService
from tests.test_commerce_facts_mysql import seed


def test_context_reads_authoritative_facts_and_is_scoped(migrated_mysql_database):
 async def scenario():
  factory,catalog,prices,stock,cats,products,skus=await seed(migrated_mysql_database)
  sessions=ShoppingSessionService(factory);owner=make_principal('test','owner');other=make_principal('test','other')
  session=await sessions.create(owner)
  app=FastAPI();app.include_router(router);app.state.runtime=SimpleNamespace(shopping_sessions=sessions,catalog=catalog,pricing=prices,inventory=stock,catalog_error=None)
  app.dependency_overrides[require_principal]=lambda:owner
  path=f'/api/v1/commerce/sessions/{session.session_id}/product-context'
  async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
   body={'product_id':products[0].id,'sku_id':skus[0].id}
   r=await client.post(path,json=body);assert r.status_code==200
   assert r.json()['quote']['price']['amount_minor']==100
   assert (await sessions.seen(session.session_id,owner))[1]=={skus[0].id}
   assert (await client.post(path,json={**body,'amount_minor':1})).status_code==422
   assert (await client.post(path,json={**body,'sku_id':skus[1].id})).status_code==422
   await catalog.archive_product(products[3].id,expected_version=(await catalog.get_product(products[3].id)).version)
   assert (await client.post(path,json={'product_id':products[3].id,'sku_id':skus[3].id})).status_code==422
   app.dependency_overrides[require_principal]=lambda:other
   assert (await client.post(path,json=body)).status_code==404
   app.dependency_overrides.clear();assert (await client.post(path,json=body)).status_code==401
 asyncio.run(scenario())
