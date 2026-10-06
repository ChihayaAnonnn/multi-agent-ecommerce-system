"""交易路由必须先验证真实 Bearer 主体。"""
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.commerce import router
from ecom.infra.db import create_session_factory
from ecom.modules.sessions.service import ShoppingSessionService
from tests.test_auth_api import (
    _login,
    _register,
    bearer,
    build_app,
    build_runtime,
    http_client,
    identity_settings,
)
from tests.test_auth_api import (
    # pytest 按模块命名空间解析 fixture；`as` 形式声明为显式再导出
    identity_key_files as identity_key_files,
)


def test_sessions_require_bearer_and_reject_self_reported_seen():
    async def scenario():
        app = FastAPI(); app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app), base_url='https://test') as client:
            assert (await client.post('/api/v1/commerce/sessions')).status_code == 401
            assert (await client.post('/api/v1/commerce/sessions/x/seen', json={'product_ids':['p']})).status_code == 404
    asyncio.run(scenario())



def test_real_accounts_are_isolated_after_refresh(migrated_mysql_database, clean_redis, identity_key_files):
    async def scenario():
        settings = identity_settings(identity_key_files)
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        runtime.shopping_sessions = ShoppingSessionService(create_session_factory(migrated_mysql_database))
        app = build_app(runtime, settings); app.include_router(router)
        try:
            async with http_client(app) as a, http_client(app) as b:
                assert (await _register(a, 'owner@example.com')).status_code == 201
                assert (await _register(b, 'other@example.com')).status_code == 201
                auth_a = (await _login(a, 'owner@example.com')).json()
                auth_b = (await _login(b, 'other@example.com')).json()
                created = await a.post('/api/v1/commerce/sessions', headers=bearer(auth_a['access_token']))
                assert created.status_code == 200
                sid = created.json()['session_id']
                assert (await b.get(f'/api/v1/commerce/sessions/{sid}', headers=bearer(auth_b['access_token']))).status_code == 404
                refresh = await a.post('/api/v1/auth/refresh', headers={'X-CSRF-Token':auth_a['csrf_token']})
                assert refresh.status_code == 200
                assert (await a.get(f'/api/v1/commerce/sessions/{sid}', headers=bearer(refresh.json()['access_token']))).status_code == 200
                forged = await a.post('/api/v1/commerce/sessions', headers=bearer(auth_a['access_token']), json={'user_id':'other'})
                assert forged.status_code == 422
        finally:
            await runtime.redis.aclose()
    asyncio.run(scenario())
