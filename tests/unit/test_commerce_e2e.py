"""演示页在生产环境绝不挂载。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from ecom.api.routes.demo import configure_commerce_demo

DEMO_DIR = Path(__file__).resolve().parents[2] / 'demo'


def test_demo_is_never_mounted_in_production():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    async def scenario():
        app = FastAPI()
        configure_commerce_demo(
            app,
            SimpleNamespace(
                identity_environment='production', commerce_demo_enabled=True,
            ),
            demo_dir=DEMO_DIR,
        )
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            assert (await client.get('/demo/commerce')).status_code==404
    asyncio.run(scenario())
