"""读车必须先校验会话归属。"""
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.commerce import router


def test_cart_requires_bearer():
    async def scenario():
        app=FastAPI();app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            assert (await client.get('/api/v1/commerce/sessions/x/cart')).status_code==401
    asyncio.run(scenario())
