"""价格领域：精确金额、版本更新与并发写入的 MySQL 测试。"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from catalog.schemas import CategoryInput, ProductInput, SKUInput
from catalog.service import CatalogConflictError, CatalogService, CatalogSKUReader
from db.base import utcnow
from db.session import create_session_factory
from pricing.models import SKUPrice, SKUPriceChange
from pricing.service import (
    PricingConflictError,
    PricingNotFoundError,
    PricingService,
    PricingValidationError,
    to_minor_units,
)
from tests.conftest import downgrade_to, read_schema, upgrade_to

PRICING_TABLES = {"sku_prices", "sku_price_changes"}
CATALOG_TABLES = {
    "categories",
    "category_attributes",
    "products",
    "product_options",
    "option_values",
    "skus",
    "sku_option_values",
    "product_media",
}


def test_pricing_migration_adds_tables_and_downgrades(migrated_mysql_database: str):
    """0002 迁移新增价格表，并可从 0001 升级、降回 0001。"""
    schema = read_schema(migrated_mysql_database)
    assert PRICING_TABLES <= schema["tables"]
    assert ("sku_price_changes", ("currency", "price_version", "sku_id")) in schema["uniques"]
    assert "old_amount_minor" in schema["columns"]["sku_price_changes"]

    # 降回 0001 只保留目录表
    downgrade_to(migrated_mysql_database, "0001_catalog")
    assert read_schema(migrated_mysql_database)["tables"] - {"alembic_version"} == CATALOG_TABLES

    # 再升到 0002
    upgrade_to(migrated_mysql_database, "0002_pricing")
    assert PRICING_TABLES <= read_schema(migrated_mysql_database)["tables"]


# ---------- 测试辅助 ----------


async def _create_sku(database_url: str, code: str = "P-1-000") -> str:
    """建一个最小可售单元，返回 SKU ID。"""
    service = CatalogService(create_session_factory(database_url))
    category = await service.create_category(CategoryInput(code=f"c-{code}", name="手机"))
    product = await service.create_product(
        ProductInput(category_id=category.id, product_code=f"p-{code}", name="商品")
    )
    sku = await service.create_sku(product.id, SKUInput(sku_code=code))
    return sku.id


async def _scalar(database_url: str, statement):
    async with create_session_factory(database_url)() as session:
        return await session.scalar(statement)


async def _history(database_url: str, sku_id: str):
    async with create_session_factory(database_url)() as session:
        rows = await session.scalars(
            select(SKUPriceChange)
            .where(SKUPriceChange.sku_id == sku_id)
            .order_by(SKUPriceChange.price_version)
        )
        return [
            {
                "version": row.price_version,
                "old": row.old_amount_minor,
                "new": row.new_amount_minor,
                "reason": row.reason,
            }
            for row in rows
        ]


# ---------- 金额与币种 ----------


def test_yuan_decimal_conversion_and_read_back(migrated_mysql_database: str):
    """1 元等于 100 分；读取返回金额、币种、版本与读取时间。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))

        first = await pricing.set_price(sku_id, Decimal("12.99"), None, "首次定价")
        assert first.amount_minor == 1299
        assert first.currency == "CNY"
        assert first.version == 1

        assert to_minor_units(Decimal("1")) == 100
        assert to_minor_units(Decimal("1.5")) == 150
        assert to_minor_units(4200) == 4200

        prices = await pricing.get_prices([sku_id])
        assert prices[sku_id].amount_minor == 1299
        assert prices[sku_id].version == 1
        assert prices[sku_id].read_at >= prices[sku_id].effective_at

    asyncio.run(scenario())


def test_rejects_float_negative_and_imprecise_amounts(migrated_mysql_database: str):
    """拒绝浮点数、负数与超过两位小数的 Decimal，且不写任何行。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))

        for bad_amount in (12.99, Decimal("1.005"), -1, True):
            with pytest.raises(PricingValidationError):
                await pricing.set_price(sku_id, bad_amount, None, "非法金额")

        assert await _scalar(
            migrated_mysql_database,
            select(func.count()).select_from(SKUPrice).where(SKUPrice.sku_id == sku_id),
        ) == 0
        assert await _history(migrated_mysql_database, sku_id) == []

    asyncio.run(scenario())


def test_non_cny_price_is_not_treated_as_current(migrated_mysql_database: str):
    """首版只认 CNY：其他币种的价格行不算现行价，上架前必须另有 CNY 价格。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))

        async with create_session_factory(migrated_mysql_database)() as session:
            session.add(
                SKUPrice(
                    sku_id=sku_id,
                    currency="USD",
                    amount_minor=999,
                    version=1,
                    effective_at=utcnow(),
                )
            )
            await session.commit()

        assert await pricing.get_prices([sku_id]) == {}
        assert await pricing.has_current_prices([sku_id]) is False

        await pricing.set_price(sku_id, 1299, None, "补 CNY 价格")
        assert await pricing.has_current_prices([sku_id]) is True

    asyncio.run(scenario())


# ---------- 版本与历史 ----------


def test_missing_price_is_not_backfilled(migrated_mysql_database: str):
    """无价 SKU 不出现在结果里，也不会回填旧价。"""

    async def scenario():
        priced_sku = await _create_sku(migrated_mysql_database, code="P-1-000")
        unpriced_sku = await _create_sku(migrated_mysql_database, code="P-2-000")
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))
        await pricing.set_price(priced_sku, 1000, None, "首次定价")

        prices = await pricing.get_prices([priced_sku, unpriced_sku])
        assert set(prices) == {priced_sku}
        assert await pricing.has_current_prices([priced_sku, unpriced_sku]) is False
        assert await pricing.has_current_prices([priced_sku]) is True

    asyncio.run(scenario())


def test_version_mismatch_does_not_write_history(migrated_mysql_database: str):
    """版本不匹配时不覆盖现行价，也不追加历史。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))
        await pricing.set_price(sku_id, 1000, None, "首次定价")
        await pricing.set_price(sku_id, 1200, 1, "调价")

        with pytest.raises(PricingConflictError):
            await pricing.set_price(sku_id, 1500, 1, "过期版本写入")

        prices = await pricing.get_prices([sku_id])
        assert prices[sku_id].amount_minor == 1200
        assert prices[sku_id].version == 2
        assert await _history(migrated_mysql_database, sku_id) == [
            {"version": 1, "old": None, "new": 1000, "reason": "首次定价"},
            {"version": 2, "old": 1000, "new": 1200, "reason": "调价"},
        ]

        # 已经存在价格时，expected_version 不能再为空
        with pytest.raises(PricingConflictError):
            await pricing.set_price(sku_id, 1300, None, "缺少版本")
        # 尚无价格时，expected_version 也不能有值
        fresh_sku = await _create_sku(migrated_mysql_database, code="P-3-000")
        with pytest.raises(PricingConflictError):
            await pricing.set_price(fresh_sku, 1300, 1, "多余版本")

    asyncio.run(scenario())


def test_concurrent_writes_with_same_old_version_only_one_wins(
    migrated_mysql_database: str,
):
    """持有相同旧版本的并发写入只能成功一个，历史里新版本只出现一次。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))
        await pricing.set_price(sku_id, 1000, None, "首次定价")

        results = await asyncio.gather(
            pricing.set_price(sku_id, 1100, 1, "并发写入 A"),
            pricing.set_price(sku_id, 1200, 1, "并发写入 B"),
            return_exceptions=True,
        )

        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], PricingConflictError)

        prices = await pricing.get_prices([sku_id])
        assert prices[sku_id].version == 2
        assert prices[sku_id].amount_minor == successes[0].amount_minor

        history = await _history(migrated_mysql_database, sku_id)
        assert [item["version"] for item in history] == [1, 2]
        assert history[1]["new"] == prices[sku_id].amount_minor

    asyncio.run(scenario())


def test_unknown_sku_does_not_write_price(migrated_mysql_database: str):
    """无效 SKU 不写价格，也不写历史。"""

    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))
        with pytest.raises(PricingNotFoundError):
            await pricing.set_price("no-such-sku", 1000, None, "无效 SKU")

        assert await _scalar(
            migrated_mysql_database, select(func.count()).select_from(SKUPrice)
        ) == 0
        assert await pricing.get_prices(["no-such-sku"]) == {}

    asyncio.run(scenario())


# ---------- 与目录上架边界的集成 ----------


def test_catalog_activation_uses_real_pricing_service(migrated_mysql_database: str):
    """目录上架使用真实价格服务：无价不能上架，定价后才能上架。"""

    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        pricing = PricingService(factory, CatalogSKUReader(factory))
        catalog = CatalogService(factory, price_reader=pricing)

        category = await catalog.create_category(CategoryInput(code="c-1", name="手机"))
        product = await catalog.create_product(
            ProductInput(category_id=category.id, product_code="P-1", name="商品")
        )
        sku = await catalog.create_sku(product.id, SKUInput(sku_code="P-1-000"))

        with pytest.raises(CatalogConflictError):
            await catalog.activate_product(product.id, expected_version=product.version)

        await pricing.set_price(sku.id, Decimal("199.00"), None, "首次定价")
        activated = await catalog.activate_product(
            product.id, expected_version=product.version
        )
        assert activated.status == "active"

        stored = await _scalar(
            migrated_mysql_database, select(SKUPrice).where(SKUPrice.sku_id == sku.id)
        )
        assert stored.amount_minor == 19900

    asyncio.run(scenario())
