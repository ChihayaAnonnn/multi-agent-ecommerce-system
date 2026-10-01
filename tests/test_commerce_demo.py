"""桌面聊天演示的入口、访客身份与主体隔离。"""
import asyncio
from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.commerce import router
from api.commerce_demo import configure_commerce_demo
from db.session import create_session_factory
from shopping_session.service import ShoppingSessionService
from tests.test_auth_api import (
    identity_key_files, identity_settings, build_runtime, build_app, http_client, bearer,
)


def test_demo_has_only_chat_input_and_collapsible_history():
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inputs = []
            self.ids = set()
        def handle_starttag(self, tag, attrs):
            fields = dict(attrs)
            self.ids.add(fields.get('id'))
            if tag in {'input', 'select', 'textarea'}:
                self.inputs.append(fields.get('id'))
    parser = Controls()
    parser.feed((Path(__file__).parents[1] / 'demo/commerce.html').read_text())
    assert parser.inputs == ['message']
    assert {'sidebar', 'toggle-sidebar', 'history', 'messages', 'new-session'} <= parser.ids
    assert not {'login-form', 'auth', 'password', 'email', 'quantity'} & parser.ids


def test_guest_entry_is_unavailable_outside_explicit_demo(identity_key_files):
    async def scenario():
        for enabled, environment in [(False, 'demo'), (True, 'production')]:
            app = FastAPI()
            configure_commerce_demo(app, identity_settings(identity_key_files,
                commerce_demo_enabled=enabled, identity_environment=environment))
            async with AsyncClient(transport=ASGITransport(app), base_url='https://test') as client:
                assert (await client.post('/demo/commerce/guest')).status_code == 404
    asyncio.run(scenario())


def test_guest_auto_login_refresh_and_resource_isolation(migrated_mysql_database, clean_redis, identity_key_files):
    async def scenario():
        settings = identity_settings(identity_key_files, commerce_demo_enabled=True)
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        runtime.shopping_sessions = ShoppingSessionService(create_session_factory(migrated_mysql_database))
        app = build_app(runtime, settings)
        app.include_router(router)
        configure_commerce_demo(app, settings)
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
