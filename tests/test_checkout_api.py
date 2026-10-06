"""预览和宿主交接均要求 Bearer。"""
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.routes.commerce import router


def test_checkout_routes_require_bearer():
    async def scenario():
        app=FastAPI();app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            for route,body in [('checkout-preview',{'cart_version':1}),('checkout-handoff',{'action_id':'a','approved':True,'idempotency_key':'key'})]:
                response=await client.post('/api/v1/commerce/sessions/x/'+route,json=body)
                assert response.status_code==401
    asyncio.run(scenario())
