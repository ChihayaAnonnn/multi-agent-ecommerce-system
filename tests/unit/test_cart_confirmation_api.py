"""确认入口的凭证门禁。"""
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.commerce import router


def test_confirm_requires_bearer():
    async def scenario():
        app=FastAPI();app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            r=await client.post('/api/v1/commerce/sessions/x/actions/a/confirm',json={'approved':True,'idempotency_key':'key'})
            assert r.status_code==401
    asyncio.run(scenario())
