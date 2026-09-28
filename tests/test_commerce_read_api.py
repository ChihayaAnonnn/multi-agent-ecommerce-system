"""消费者只读接口：目录读取、SKU 报价与旧模型适配器。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.runtime import ApiRuntime, create_runtime
from catalog.legacy_adapter import to_legacy_product
from catalog.schemas import CategoryInput, OptionInput, OptionValueInput, ProductInput, SKUInput
from catalog.service import CatalogService
from db.session import create_session_factory
from inventory.models import DEFAULT_LOCATION_ID
from inventory.service import InventoryDependencyError, InventoryService
from pricing.service import PricingService
from services.ab_test import ABTestEngine
from services.metrics import MetricsCollector


def build_app(runtime) -> FastAPI:
    from api.catalog import router as catalog_router
    from api.commerce_read import router as commerce_read_router

    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(commerce_read_router)
    app.state.runtime = runtime
    return app


async def arequest(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method, path, **kwargs):
    """同步调用；在 asyncio 场景内请直接用 arequest。"""
    return asyncio.run(arequest(app, method, path, **kwargs))


def mysql_runtime(database_url: str, inventory=None) -> ApiRuntime:
    factory = create_session_factory(database_url)
    pricing = PricingService(factory)
    return SimpleNamespace(
        ab_engine=ABTestEngine(),
        metrics_collector=MetricsCollector(),
        supervisor=None,
        rec_graph=None,
        catalog=CatalogService(factory, price_reader=pricing),
        pricing=pricing,
        inventory=inventory or InventoryService(factory),
        catalog_error=None,
    )


async def _seed_catalog(database_url: str):
    """建一个带规格与 SKU 的草稿商品（尚无价格），返回目录服务与关键 ID。

    刻意不上架：上架需要现行价格，由各用例按需定价后再调用 activate_product。
    """
    factory = create_session_factory(database_url)
    catalog = CatalogService(factory, price_reader=PricingService(factory))
    category = await catalog.create_category(CategoryInput(code="c-phone", name="手机"))
    product = await catalog.create_product(
        ProductInput(category_id=category.id, product_code="P-1", name="测试手机")
    )
    color = await catalog.add_option(
        product.id, OptionInput(code="color", name="颜色", sort_order=1)
    )
    black = await catalog.add_option_value(
        color.id, OptionValueInput(code="black", display_value="黑色")
    )
    sku = await catalog.create_sku(
        product.id, SKUInput(sku_code="P-1-B", option_value_ids=[black.id])
    )
    return catalog, product, sku


class BrokenInventory(InventoryService):
    """模拟库存后端故障。"""

    def __init__(self):
        pass

    async def get_availability(self, sku_ids):
        raise InventoryDependencyError("库存查询失败")


# ---------- 目录读取 ----------


def test_list_and_detail_expose_product_sku_hierarchy(migrated_mysql_database: str):
    """列表返回商品摘要，详情返回 Product/SKU 分层，金额与状态来自真实数据。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        # 上架需要现行价格
        await runtime.pricing.set_price(sku.id, 1299, None, "首次定价")
        await catalog.activate_product(product.id, expected_version=product.version)
        app = build_app(runtime)

        listing = await arequest(app, "GET", "/api/v1/catalog/products", params={"limit": 10})
        assert listing.status_code == 200
        page = listing.json()
        assert [item["product_code"] for item in page["items"]] == ["P-1"]
        assert page["items"][0]["status"] == "active"
        assert "skus" not in page["items"][0]

        detail = await arequest(app, "GET", f"/api/v1/catalog/products/{product.id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["category"]["name"] == "手机"
        assert [item["sku_code"] for item in body["skus"]] == ["P-1-B"]
        assert body["skus"][0]["selected_values"][0]["display_value"] == "黑色"

        single = await arequest(app, "GET", f"/api/v1/catalog/skus/{sku.id}")
        assert single.status_code == 200
        assert single.json()["option_signature"] == sku.option_signature

        assert (await arequest(app, "GET", "/api/v1/catalog/skus/no-such-sku")).status_code == 404
        assert (await arequest(app, "GET", "/api/v1/catalog/products/no-such")).status_code == 404
        assert (
            await arequest(app, "GET", "/api/v1/catalog/products", params={"limit": 101})
        ).status_code == 422

    asyncio.run(scenario())


def test_read_endpoints_return_503_when_database_is_not_configured():
    """未配置数据库时，新只读端点返回 503，并且不泄露连接串。"""
    runtime = create_runtime()
    runtime.catalog = None
    runtime.pricing = None
    runtime.inventory = None
    runtime.catalog_error = "数据库未配置：请将 ECOM_DATABASE_URL 设置为 mysql+asyncmy:// URL"
    app = build_app(runtime)

    response = request(app, "GET", "/api/v1/catalog/products")
    assert response.status_code == 503
    assert "ECOM_DATABASE_URL" in response.json()["detail"]
    assert "password" not in response.json()["detail"]


# ---------- SKU 报价 ----------


def test_quote_reports_exact_minor_amount_and_purchasable_state(
    migrated_mysql_database: str,
):
    """报价金额精确到分，价格与库存版本一并返回。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        _, _, sku = await _seed_catalog(migrated_mysql_database)
        pricing = runtime.pricing
        inventory = runtime.inventory

        await inventory.create_balance(sku.id)
        app = build_app(runtime)

        # 尚无价格
        unpriced = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert unpriced["purchasable"] is False
        assert unpriced["reason"] == "price_missing"
        assert unpriced["price"] is None

        await pricing.set_price(sku.id, Decimal("12.99"), None, "首次定价")
        await inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 4, "key-in", "入库")

        quote = await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")
        assert quote.status_code == 200
        body = quote.json()
        assert body["purchasable"] is True
        assert body["reason"] == "purchasable"
        assert body["price"]["amount_minor"] == 1299
        assert body["price"]["currency"] == "CNY"
        assert body["price"]["version"] == 1
        assert body["availability"]["status"] == "available"
        assert body["availability"]["available_quantity"] == 4
        assert body["availability"]["version"] == 2
        assert body["read_at"] >= body["availability"]["read_at"]

    asyncio.run(scenario())


def test_quote_distinguishes_missing_price_zero_stock_unknown_stock_and_failure(
    migrated_mysql_database: str,
):
    """缺价、零库存、未知库存与后端故障是不同状态，且都不可购买。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        _, _, sku = await _seed_catalog(migrated_mysql_database)
        pricing, inventory = runtime.pricing, runtime.inventory
        app = build_app(runtime)

        # 未知库存（没有余额行）+ 有价格
        await pricing.set_price(sku.id, 1299, None, "首次定价")
        unknown = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert unknown["reason"] == "stock_unknown"
        assert unknown["availability"]["available_quantity"] is None
        assert unknown["purchasable"] is False

        # 零库存
        await inventory.create_balance(sku.id)
        zero = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert zero["reason"] == "out_of_stock"
        assert zero["availability"]["available_quantity"] == 0
        assert zero["purchasable"] is False

        # 四个原因码互不相同
        assert len({unknown["reason"], zero["reason"], "price_missing", "purchasable"}) == 4

        # 后端故障：503，且不返回可购买
        broken_app = build_app(mysql_runtime(migrated_mysql_database, inventory=BrokenInventory()))
        failure = await arequest(broken_app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")
        assert failure.status_code == 503
        assert "purchasable" not in failure.json()

        # 不存在的 SKU 是 404，而不是 503
        assert (
            await arequest(app, "GET", "/api/v1/commerce/skus/ghost/quote")
        ).status_code == 404

    asyncio.run(scenario())


def test_quote_marks_inactive_sku_unpurchasable(migrated_mysql_database: str):
    """停用 SKU 即使有价格有库存也不可购买。"""

    async def scenario():
        from catalog.models import SKU

        runtime = mysql_runtime(migrated_mysql_database)
        _, _, sku = await _seed_catalog(migrated_mysql_database)
        await runtime.pricing.set_price(sku.id, 1000, None, "首次定价")
        await runtime.inventory.create_balance(sku.id)
        await runtime.inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 3, "key-in", "入库")

        async with create_session_factory(migrated_mysql_database)() as session:
            stored = await session.get(SKU, sku.id)
            stored.status = "inactive"
            await session.commit()

        body = (
            await arequest(build_app(runtime), "GET", f"/api/v1/commerce/skus/{sku.id}/quote")
        ).json()
        assert body["purchasable"] is False
        assert body["reason"] == "sku_inactive"

    asyncio.run(scenario())


# ---------- 旧模型适配器 ----------


def test_legacy_adapter_converts_minor_units_to_display_price(
    migrated_mysql_database: str,
):
    """适配器把整数分换算成旧模型的展示价，并保留旧响应字段形状。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        await runtime.pricing.set_price(sku.id, 1299, None, "首次定价")
        await runtime.inventory.create_balance(sku.id)
        await runtime.inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 6, "key-in", "入库")

        detail = await catalog.get_product(product.id)
        price = (await runtime.pricing.get_prices([sku.id]))[sku.id]
        availability = (await runtime.inventory.get_availability([sku.id]))[sku.id]

        legacy = to_legacy_product(detail, price, availability)
        assert legacy.price == 12.99
        assert legacy.stock == 6
        assert legacy.category == "手机"
        assert legacy.seller_id == ""
        assert legacy.product_id == product.id

        # 缺价与库存未知时不冒充有货有价
        unknown_legacy = to_legacy_product(detail, None, None)
        assert unknown_legacy.price == 0.0
        assert unknown_legacy.stock == 0

    asyncio.run(scenario())
