"""仅显式非生产环境挂载内部同源演示页。"""
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ecom.api.contracts.auth import LoginRequest, RegisterRequest
from ecom.api.routes.auth import login, register
from ecom.modules.identity.types import DEMO_ENVIRONMENTS

DEMO_HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' https:; frame-ancestors 'none'; base-uri 'self'"}


def configure_commerce_demo(
    app, settings, *, demo_dir: Path, build_dir: Path | None = None
) -> None:
    """挂载内部演示页。

    `demo_dir` 必须由组合根按仓库根解析后传入：本模块在分组搬迁后深度已变，
    不能再从 `__file__` 推仓库位置，否则会指向不存在的目录。
    """
    if not getattr(settings,'commerce_demo_enabled',False) or settings.identity_environment not in DEMO_ENVIRONMENTS:
        return

    built = build_dir or demo_dir.parent / 'frontend' / 'dist'
    react_page = built / 'index.html'
    if react_page.is_file() and (built / 'assets').is_dir():
        app.mount('/demo/commerce/assets', StaticFiles(directory=built / 'assets'), name='commerce-assets')

    @app.get('/demo/commerce/products/{product_id}',include_in_schema=False)
    async def commerce_product(product_id: str):
        if not react_page.is_file():
            from fastapi import HTTPException
            raise HTTPException(404)
        return FileResponse(react_page,headers=DEMO_HEADERS)

    @app.get('/demo/commerce',include_in_schema=False)
    async def commerce_demo():
        target = react_page if react_page.is_file() else demo_dir / 'commerce.html'
        return FileResponse(target, headers=DEMO_HEADERS)

    @app.get('/demo/commerce.js',include_in_schema=False)
    async def commerce_js():
        return FileResponse(
            demo_dir / 'commerce.js', media_type='text/javascript',
            headers=DEMO_HEADERS,
        )

    @app.get('/demo/commerce.css',include_in_schema=False)
    async def commerce_css() -> FileResponse:
        return FileResponse(
            demo_dir / 'commerce.css', media_type='text/css', headers=DEMO_HEADERS,
        )

    @app.post('/demo/commerce/guest',include_in_schema=False)
    async def commerce_guest(request: Request) -> Response:
        # 仅演示入口自动注册独立账号，复用来源校验、限流、Cookie 和正式鉴权。
        # 临时凭据不返回给浏览器，也不保存到页面或日志。
        email = f'guest-{uuid4().hex}@commerce-demo.invalid'
        password = secrets.token_urlsafe(32)
        await register(RegisterRequest(email=email, password=password), request, Response())
        return await login(LoginRequest(email=email, password=password), request)

    @app.get('/demo/commerce/handoffs/{handoff_id}',include_in_schema=False)
    async def demo_destination(handoff_id: str):
        # 公开落地页不读取交接记录，也不返回任何用户快照。
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>模拟交接</title><body><h1>模拟交接入口</h1><p>这是内部演示，不会创建订单或付款。已确认的商品摘要显示在购物准备页面。</p><a href="/demo/commerce">返回购物准备</a></body></html>',headers=DEMO_HEADERS)
