"""访客自动登录、刷新与资源隔离（真实 MySQL + Redis）。"""
import asyncio
from pathlib import Path

from ecom.api.routes.commerce import router
from ecom.api.routes.demo import configure_commerce_demo
from ecom.infra.db import create_session_factory
from ecom.modules.sessions.service import ShoppingSessionService
from tests.integration.test_auth_api import (
    bearer,
    build_app,
    build_runtime,
    http_client,
)
from tests.support.identity_helpers import identity_key_files as identity_key_files
from tests.support.identity_helpers import identity_settings

DEMO_DIR = Path(__file__).resolve().parents[2] / 'demo'


def test_guest_auto_login_refresh_and_resource_isolation(migrated_mysql_database, clean_redis, identity_key_files):
    async def scenario():
        settings = identity_settings(identity_key_files, commerce_demo_enabled=True)
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        runtime.shopping_sessions = ShoppingSessionService(create_session_factory(migrated_mysql_database))
        app = build_app(runtime, settings)
        app.include_router(router)
        configure_commerce_demo(app, settings, demo_dir=DEMO_DIR)
        try:
            async with http_client(app) as a, http_client(app) as b:
                # 不可信来源不能创建访客身份。
                rejected = await a.post('/demo/commerce/guest', headers={'Origin': 'https://evil.test'})
                assert rejected.status_code == 403
                created_a = await a.post('/demo/commerce/guest')
                created_b = await b.post('/demo/commerce/guest')
                assert created_a.status_code == created_b.status_code == 200
                auth_a, auth_b = created_a.json(), created_b.json()
                assert auth_a['user_id'] != auth_b['user_id']
                assert 'password' not in auth_a and 'refresh_token' not in auth_a
                assert 'httponly' in created_a.headers['set-cookie'].lower()
                assert 'secure' in created_a.headers['set-cookie'].lower()
                assert created_a.headers['cache-control'] == 'no-store'
                session = await a.post('/api/v1/commerce/sessions', headers=bearer(auth_a['access_token']))
                assert session.status_code == 200
                sid = session.json()['session_id']
                assert (await b.get(f'/api/v1/commerce/sessions/{sid}', headers=bearer(auth_b['access_token']))).status_code == 404
                csrf = await a.get('/api/v1/auth/csrf')
                refreshed = await a.post('/api/v1/auth/refresh', headers={'X-CSRF-Token': csrf.json()['csrf_token']})
                assert refreshed.status_code == 200
                assert refreshed.json()['user_id'] == auth_a['user_id']
                assert (await a.get(f'/api/v1/commerce/sessions/{sid}', headers=bearer(refreshed.json()['access_token']))).status_code == 200
        finally:
            await runtime.redis.aclose()
    asyncio.run(scenario())
