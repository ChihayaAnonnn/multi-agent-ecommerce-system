"""仅显式非生产环境挂载内部同源演示页。"""
from pathlib import Path
from fastapi.responses import FileResponse,HTMLResponse
from identity.types import DEMO_ENVIRONMENTS

DEMO_DIR=Path(__file__).resolve().parents[1]/'demo'
DEMO_HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' https:; frame-ancestors 'none'; base-uri 'self'"}


def configure_commerce_demo(app,settings) -> None:
    if not getattr(settings,'commerce_demo_enabled',False) or settings.identity_environment not in DEMO_ENVIRONMENTS:
        return

    @app.get('/demo/commerce',include_in_schema=False)
    async def commerce_demo():
        return FileResponse(DEMO_DIR/'commerce.html',headers=DEMO_HEADERS)

    @app.get('/demo/commerce.js',include_in_schema=False)
    async def commerce_js():
        return FileResponse(DEMO_DIR/'commerce.js',media_type='text/javascript',headers=DEMO_HEADERS)

    @app.get('/demo/commerce/handoffs/{handoff_id}',include_in_schema=False)
    async def demo_destination(handoff_id: str):
        # 公开落地页不读取交接记录，也不返回任何用户快照。
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>模拟交接</title><body><h1>模拟交接入口</h1><p>这是内部演示，不会创建订单或付款。已确认的商品摘要显示在购物准备页面。</p><a href="/demo/commerce">返回购物准备</a></body></html>',headers=DEMO_HEADERS)
