"""只读目录接口测试共用的应用装配与请求助手。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def build_app(runtime) -> FastAPI:
    from ecom.api.routes.catalog import quote_router
    from ecom.api.routes.catalog import router as catalog_router

    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(quote_router)
    app.state.runtime = runtime
    return app


async def arequest(app, method, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method, path, **kwargs):
    """同步调用；在 asyncio 场景内请直接用 arequest。"""
    return asyncio.run(arequest(app, method, path, **kwargs))
