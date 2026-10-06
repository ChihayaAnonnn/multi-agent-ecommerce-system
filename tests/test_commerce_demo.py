"""桌面聊天演示的入口、访客身份与主体隔离。"""
import asyncio
from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ecom.api.routes.commerce import router
from ecom.api.routes.demo import configure_commerce_demo
from ecom.infra.db import create_session_factory
from ecom.modules.sessions.service import ShoppingSessionService
from tests.test_auth_api import (
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

DEMO_DIR = Path(__file__).resolve().parents[1] / 'demo'


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
                commerce_demo_enabled=enabled, identity_environment=environment),
                demo_dir=DEMO_DIR)
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


def test_react_static_routes_do_not_shadow_api(tmp_path, identity_key_files):
    (tmp_path/'assets').mkdir()
    (tmp_path/'index.html').write_text('<html>React storefront</html>')
    (tmp_path/'assets'/'app-test.js').write_text('console.log("storefront")')
    async def scenario():
        app=FastAPI()
        configure_commerce_demo(app,identity_settings(identity_key_files,commerce_demo_enabled=True),demo_dir=DEMO_DIR,build_dir=tmp_path)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as c:
            page=await c.get('/demo/commerce');assert 'React storefront' in page.text
            assert 'no-store' in page.headers['cache-control']
            asset=await c.get('/demo/commerce/assets/app-test.js')
            assert asset.status_code==200 and 'javascript' in asset.headers['content-type']
            assert (await c.get('/demo/commerce/assets/missing.js')).status_code==404
            assert (await c.get('/demo/commerce/products/test-id')).status_code==200
            assert (await c.get('/demo/commerce/not-a-route')).status_code==404
            assert '模拟交接' in (await c.get('/demo/commerce/handoffs/test-id')).text
        app=FastAPI()
        configure_commerce_demo(app,identity_settings(identity_key_files,commerce_demo_enabled=True,identity_environment='production'),demo_dir=DEMO_DIR,build_dir=tmp_path)
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as c:
            assert (await c.get('/demo/commerce')).status_code==404
    asyncio.run(scenario())


def test_native_demo_assets_come_from_composition_root(identity_key_files):
    """无 React 构建时，原生演示页三件套必须从组合根给出的 demo_dir 取到。

    搬迁后 `demo_dir` 由组合根传入，路由不再从自身文件深度推断仓库位置；
    这条用例专门锁住 `.js`/`.css` 两条此前没有覆盖的路径。
    """
    missing_build = Path('/nonexistent-react-build')

    async def scenario():
        app = FastAPI()
        configure_commerce_demo(
            app,
            identity_settings(identity_key_files, commerce_demo_enabled=True),
            demo_dir=DEMO_DIR,
            build_dir=missing_build,
        )
        async with AsyncClient(
            transport=ASGITransport(app), base_url='https://test'
        ) as client:
            page = await client.get('/demo/commerce')
            assert page.status_code == 200
            assert 'text/html' in page.headers['content-type']
            assert 'no-store' in page.headers['cache-control']

            script = await client.get('/demo/commerce.js')
            assert script.status_code == 200
            assert 'javascript' in script.headers['content-type']
            assert len(script.content) > 0

            style = await client.get('/demo/commerce.css')
            assert style.status_code == 200
            assert 'text/css' in style.headers['content-type']
            assert len(style.content) > 0

    asyncio.run(scenario())
