"""消费者只读接口：目录读取、SKU 报价与旧模型适配器。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import ApiRuntime, create_runtime
from config import Settings
from infra.db import create_session_factory
from legacy.catalog_adapter import to_legacy_product
from legacy.services.ab_test import ABTestEngine
from legacy.services.metrics import MetricsCollector
from modules.catalog.schemas import (
    CategoryInput,
    OptionInput,
    OptionValueInput,
    ProductInput,
    SKUInput,
)
from modules.catalog.service import CatalogService, CatalogSKUReader
from modules.inventory.models import DEFAULT_LOCATION_ID
from modules.inventory.service import InventoryDependencyError, InventoryService
from modules.pricing.service import PricingDependencyError, PricingService


def build_app(runtime) -> FastAPI:
    from api.routes.catalog import quote_router
    from api.routes.catalog import router as catalog_router

    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(quote_router)
    app.state.runtime = runtime
    return app


async def arequest(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method, path, **kwargs):
    """同步调用；在 asyncio 场景内请直接用 arequest。"""
    return asyncio.run(arequest(app, method, path, **kwargs))


def mysql_runtime(database_url: str, inventory=None, pricing=None) -> ApiRuntime:
    factory = create_session_factory(database_url)
    sku_reader = CatalogSKUReader(factory)
    if pricing is None:
        pricing = PricingService(factory, sku_reader)
    return SimpleNamespace(
        ab_engine=ABTestEngine(),
        metrics_collector=MetricsCollector(),
        supervisor=None,
        rec_graph=None,
        catalog=CatalogService(factory, price_reader=pricing),
        pricing=pricing,
        inventory=inventory or InventoryService(factory, sku_reader),
        catalog_error=None,
    )


async def _seed_catalog(database_url: str):
    """建一个带规格与 SKU 的草稿商品（尚无价格），返回目录服务与关键 ID。

    刻意不上架：上架需要现行价格，由各用例按需定价后再调用 activate_product。
    """
    factory = create_session_factory(database_url)
    sku_reader = CatalogSKUReader(factory)
    pricing = PricingService(factory, sku_reader)
    catalog = CatalogService(factory, price_reader=pricing)
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


class BrokenInventory:
    """模拟库存后端故障。"""

    async def get_availability(self, sku_ids):
        raise InventoryDependencyError("库存查询失败")


class BrokenPricing:
    """模拟价格后端故障。"""

    async def get_prices(self, sku_ids):
        raise PricingDependencyError("价格查询失败")


def test_single_sku_quote_keeps_inventory_error_priority(migrated_mysql_database: str):
    async def scenario():
        _, _, sku = await _seed_catalog(migrated_mysql_database)
        runtime = mysql_runtime(migrated_mysql_database,
            inventory=BrokenInventory(), pricing=BrokenPricing())
        app = build_app(runtime)
        response = await arequest(app, 'GET', f'/api/v1/commerce/skus/{sku.id}/quote')
        assert response.status_code == 503
        assert response.json() == {'detail': '库存服务不可用，无法报价'}
        missing = await arequest(app, 'GET', '/api/v1/commerce/skus/missing/quote')
        assert missing.status_code == 404

    asyncio.run(scenario())


def test_single_sku_quote_keeps_independent_source_order():
    from modules.catalog.schemas import SKUDetailView
    from tests.test_commerce_facts import Inventory, Prices

    calls = []

    class Catalog:
        async def get_sku(self, sku_id):
            calls.append('catalog')
            return SKUDetailView(id=sku_id, product_id='product', sku_code='SKU',
                option_signature='default', status='active',
                product_status='active', version=1)

    class Pricing(Prices):
        async def get_prices(self, ids):
            calls.append('pricing')
            return await super().get_prices(ids)

    class Stock(Inventory):
        async def get_availability(self, ids):
            calls.append('inventory')
            return await super().get_availability(ids)

    response = request(build_app(SimpleNamespace(catalog=Catalog(), pricing=Pricing(),
        inventory=Stock())), 'GET', '/api/v1/commerce/skus/sku-1/quote')
    assert response.status_code == 200 and response.json()['purchasable']
    assert calls == ['catalog', 'inventory', 'pricing']


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
    async def scenario():
        runtime = await create_runtime(Settings(_env_file=None, database_url=""))
        try:
            response = await arequest(
                build_app(runtime), "GET", "/api/v1/catalog/products",
            )
            assert response.status_code == 503
            assert "ECOM_DATABASE_URL" in response.json()["detail"]
            assert "password" not in response.json()["detail"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def _assert_utc_timestamp(value: str) -> None:
    """解析出的时间必须带 UTC 标识，不能是无法判断时区的裸时间。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"{value} 缺少时区标识"
    assert parsed.utcoffset() == timedelta(0), f"{value} 不是 UTC"


def test_timestamps_carry_utc_designator(migrated_mysql_database: str):
    """对外时间字段一律带 UTC 标识，避免调用方按本地时间误读。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        await runtime.pricing.set_price(sku.id, 1299, None, "首次定价")
        await runtime.inventory.create_balance(sku.id)
        await catalog.activate_product(product.id, expected_version=product.version)
        app = build_app(runtime)

        listing = (await arequest(app, "GET", "/api/v1/catalog/products")).json()
        detail = (await arequest(app, "GET", f"/api/v1/catalog/products/{product.id}")).json()
        quote = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()

        timestamps = [
            listing["items"][0]["created_at"],
            listing["items"][0]["updated_at"],
            detail["created_at"],
            detail["updated_at"],
            quote["read_at"],
            quote["price"]["effective_at"],
            quote["price"]["read_at"],
            quote["availability"]["read_at"],
        ]
        for value in timestamps:
            _assert_utc_timestamp(value)

        # 报价的库位明细不该出现只属于调整结果的 replayed 标记
        assert "replayed" not in quote["availability"]["locations"][0]

    asyncio.run(scenario())


# ---------- SKU 报价 ----------


def test_quote_reports_exact_minor_amount_and_purchasable_state(
    migrated_mysql_database: str,
):
    """报价金额精确到分，价格与库存版本一并返回。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        pricing = runtime.pricing
        inventory = runtime.inventory

        await pricing.set_price(sku.id, Decimal("12.99"), None, "首次定价")
        await inventory.create_balance(sku.id)
        await inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 4, "key-in", "入库")
        # 可购买的前提是商品已上架，而不只是 SKU 有价有货
        await catalog.activate_product(product.id, expected_version=product.version)
        app = build_app(runtime)

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


def test_quote_requires_active_product(migrated_mysql_database: str):
    """草稿或已归档商品不得报价为可购买，即使 SKU 有价有货。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        await runtime.pricing.set_price(sku.id, 1299, None, "首次定价")
        await runtime.inventory.create_balance(sku.id)
        await runtime.inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 3, "key-in", "入库")
        app = build_app(runtime)

        # 草稿商品：有价有货也不可购买，且原因与「缺价」「缺货」不同
        draft = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert draft["purchasable"] is False
        assert draft["reason"] == "product_not_active"
        assert draft["sku"]["product_status"] == "draft"
        assert draft["price"]["amount_minor"] == 1299
        assert draft["availability"]["status"] == "available"

        # 上架后可购买
        active = await catalog.activate_product(product.id, expected_version=product.version)
        published = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert published["purchasable"] is True
        assert published["sku"]["product_status"] == "active"

        # 归档后再次不可购买
        await catalog.archive_product(product.id, expected_version=active.version)
        archived = (await arequest(app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")).json()
        assert archived["purchasable"] is False
        assert archived["reason"] == "product_not_active"
        assert archived["sku"]["product_status"] == "archived"

    asyncio.run(scenario())


def test_quote_distinguishes_missing_price_zero_stock_unknown_stock_and_failure(
    migrated_mysql_database: str,
):
    """缺价、零库存、未知库存与后端故障是不同状态，且都不可购买。"""

    async def scenario():
        runtime = mysql_runtime(migrated_mysql_database)
        catalog, product, sku = await _seed_catalog(migrated_mysql_database)
        pricing, inventory = runtime.pricing, runtime.inventory

        # 先让商品带着一个「有价有货」的 SKU 上架
        await pricing.set_price(sku.id, 1299, None, "首次定价")
        await inventory.create_balance(sku.id)
        await inventory.adjust(sku.id, DEFAULT_LOCATION_ID, 2, "key-in", "入库")
        await catalog.activate_product(product.id, expected_version=product.version)
        app = build_app(runtime)

        # 上架后新增一个尚未定价的 SKU：上架要求有价，所以缺价只能这样出现
        detail = await catalog.get_product(product.id)
        white = await catalog.add_option_value(
            detail.options[0].id,
            OptionValueInput(code="white", display_value="白色", sort_order=2),
        )
        second = await catalog.create_sku(
            product.id, SKUInput(sku_code="P-1-W", option_value_ids=[white.id])
        )

        missing_price = (
            await arequest(app, "GET", f"/api/v1/commerce/skus/{second.id}/quote")
        ).json()
        assert missing_price["reason"] == "price_missing"
        assert missing_price["price"] is None
        assert missing_price["purchasable"] is False

        # 定价后仍没有余额行：库存未知
        await pricing.set_price(second.id, 1500, None, "补定价")
        unknown = (
            await arequest(app, "GET", f"/api/v1/commerce/skus/{second.id}/quote")
        ).json()
        assert unknown["reason"] == "stock_unknown"
        assert unknown["availability"]["available_quantity"] is None
        assert unknown["purchasable"] is False

        # 建档但零库存
        await inventory.create_balance(second.id)
        zero = (await arequest(app, "GET", f"/api/v1/commerce/skus/{second.id}/quote")).json()
        assert zero["reason"] == "out_of_stock"
        assert zero["availability"]["available_quantity"] == 0
        assert zero["purchasable"] is False

        # 入库后可购买
        await inventory.adjust(second.id, DEFAULT_LOCATION_ID, 3, "key-in-2", "入库")
        ok = (await arequest(app, "GET", f"/api/v1/commerce/skus/{second.id}/quote")).json()
        assert ok["reason"] == "purchasable"

        # 四个原因码互不相同
        assert len(
            {missing_price["reason"], unknown["reason"], zero["reason"], ok["reason"]}
        ) == 4

        # 库存后端故障：503，且不返回可购买
        broken_app = build_app(mysql_runtime(migrated_mysql_database, inventory=BrokenInventory()))
        failure = await arequest(broken_app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote")
        assert failure.status_code == 503
        assert "purchasable" not in failure.json()

        # 价格后端故障：同样是 503，不能落到未处理异常
        broken_pricing_app = build_app(
            mysql_runtime(migrated_mysql_database, pricing=BrokenPricing())
        )
        pricing_failure = await arequest(
            broken_pricing_app, "GET", f"/api/v1/commerce/skus/{sku.id}/quote"
        )
        assert pricing_failure.status_code == 503
        assert "purchasable" not in pricing_failure.json()

        # 不存在的 SKU 是 404，而不是 503
        assert (
            await arequest(app, "GET", "/api/v1/commerce/skus/ghost/quote")
        ).status_code == 404

    asyncio.run(scenario())


def test_quote_marks_inactive_sku_unpurchasable(migrated_mysql_database: str):
    """停用 SKU 即使有价格有库存也不可购买。"""

    async def scenario():
        from modules.catalog.models import SKU

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
