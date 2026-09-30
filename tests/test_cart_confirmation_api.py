"""确认入口的凭证门禁。"""
import asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from api.commerce import router


def test_confirm_requires_bearer():
    async def scenario():
        app=FastAPI();app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            r=await client.post('/api/v1/commerce/sessions/x/actions/a/confirm',json={'approved':True,'idempotency_key':'key'})
            assert r.status_code==401
    asyncio.run(scenario())

from types import SimpleNamespace
from api.identity import require_principal
from commerce_orchestrator.service import CommerceOrchestrator
from tests.test_cart_confirmation_mysql import setup


def test_turn_proposes_and_button_confirms_once(migrated_mysql_database):
    async def scenario():
        _,sessions,cart,a,_,s,_,_,_,skus=await setup(migrated_mysql_database)
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[require_principal]=lambda:a
        app.state.runtime=SimpleNamespace(shopping_sessions=sessions,cart=cart,
            commerce=CommerceOrchestrator(sessions,cart.facts,cart.facts.catalog))
        app.state.runtime.commerce.cart=cart
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            path=f'/api/v1/commerce/sessions/{s.session_id}'
            response=await client.post(path+'/turns',json={'turn_id':'add','message':'加入购物车',
                'selected_sku_id':skus[0].id,'quantity':1,'expected_cart_version':1})
            assert response.status_code==200,response.text
            body=response.json();assert body['status']=='confirm_action'
            assert not (await cart.get_cart(a)).items
            aid=body['proposed_action']['action_id']
            response=await client.post(path+f'/actions/{aid}/confirm',json={'approved':True,'idempotency_key':'button'})
            assert response.status_code==200,response.text
            assert response.json()['cart']['items'][0]['quantity']==1
            replay=await client.post(path+f'/actions/{aid}/confirm',json={'approved':True,'idempotency_key':'button'})
            assert replay.json()['replayed'] and replay.json()['cart']['version']==2
    asyncio.run(scenario())
