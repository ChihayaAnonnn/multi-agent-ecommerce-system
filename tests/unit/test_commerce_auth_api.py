"""交易路由必须先验证真实 Bearer 主体。"""
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.commerce import router


def test_sessions_require_bearer_and_reject_self_reported_seen():
    async def scenario():
        app = FastAPI(); app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app), base_url='https://test') as client:
            assert (await client.post('/api/v1/commerce/sessions')).status_code == 401
            assert (await client.post('/api/v1/commerce/sessions/x/seen', json={'product_ids':['p']})).status_code == 404
    asyncio.run(scenario())
