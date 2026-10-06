"""库存领域：余额、流水、幂等与并发调整的 MySQL 测试。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from ecom.infra.db import create_session_factory
from ecom.modules.catalog.schemas import CategoryInput, ProductInput, SKUInput
from ecom.modules.catalog.service import CatalogService, CatalogSKUReader
from ecom.modules.inventory.models import DEFAULT_LOCATION_ID, InventoryMovement
from ecom.modules.inventory.service import (
    InventoryConflictError,
    InventoryDependencyError,
    InventoryNotFoundError,
    InventoryService,
    InventoryValidationError,
)
from tests.conftest import downgrade_to, read_schema, upgrade_to

INVENTORY_TABLES = {"stock_locations", "inventory_balances", "inventory_movements"}


def test_inventory_migration_adds_tables_and_seeds_default_location(
    migrated_mysql_database: str,
):
    """0003 迁移新增库存表与默认库位，并可降回 0002。"""
    schema = read_schema(migrated_mysql_database)
    assert INVENTORY_TABLES <= schema["tables"]
    assert ("stock_locations", ("code",)) in schema["uniques"]
    assert ("inventory_movements", ("idempotency_key",)) in schema["uniques"]
    assert (
        "inventory_movements",
        ("balance_version", "location_id", "sku_id"),
    ) in schema["uniques"]

    async def check_seed():
        async with create_session_factory(migrated_mysql_database)() as session:
            from ecom.modules.inventory.models import StockLocation

            location = await session.get(StockLocation, DEFAULT_LOCATION_ID)
            assert location is not None
            assert location.code == "default"
            assert location.status == "active"

    asyncio.run(check_seed())

    downgrade_to(migrated_mysql_database, "0002_pricing")
    assert INVENTORY_TABLES.isdisjoint(read_schema(migrated_mysql_database)["tables"])
    upgrade_to(migrated_mysql_database, "0003_inventory")
    assert INVENTORY_TABLES <= read_schema(migrated_mysql_database)["tables"]


# ---------- 测试辅助 ----------


async def _create_sku(database_url: str, code: str = "P-1-000") -> str:
    service = CatalogService(create_session_factory(database_url))
    category = await service.create_category(CategoryInput(code=f"c-{code}", name="手机"))
    product = await service.create_product(
        ProductInput(category_id=category.id, product_code=f"p-{code}", name="商品")
    )
    sku = await service.create_sku(product.id, SKUInput(sku_code=code))
    return sku.id


async def _movements(database_url: str, sku_id: str):
    async with create_session_factory(database_url)() as session:
        rows = await session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.sku_id == sku_id)
            .order_by(InventoryMovement.balance_version)
        )
        return [(row.delta, row.balance_version, row.idempotency_key) for row in rows]


async def _location(database_url: str, code: str, name: str) -> str:
    from ecom.modules.inventory.models import StockLocation

    async with create_session_factory(database_url)() as session:
        location = StockLocation(id=f"loc-{code}", code=code, name=name)
        session.add(location)
        await session.commit()
        return location.id


# ---------- 余额与调整 ----------


def test_adjust_from_zero_balance_records_movement(migrated_mysql_database: str):
    """从零余额入库，余额与流水在同一事务内更新。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))

        created = await service.create_balance(sku_id)
        assert (created.on_hand, created.reserved, created.available, created.version) == (0, 0, 0, 1)

        inbound = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 8, "key-in-1", "采购入库")
        assert (inbound.on_hand, inbound.available, inbound.version) == (8, 8, 2)

        outbound = await service.adjust(sku_id, DEFAULT_LOCATION_ID, -3, "key-out-1", "订单出库")
        assert (outbound.on_hand, outbound.available, outbound.version) == (5, 5, 3)

        assert await _movements(migrated_mysql_database, sku_id) == [
            (8, 2, "key-in-1"),
            (-3, 3, "key-out-1"),
        ]

    asyncio.run(scenario())


def test_insufficient_stock_is_rejected_without_movement(migrated_mysql_database: str):
    """库存不足时拒绝出库，且不写流水、不改余额。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))
        await service.create_balance(sku_id)
        await service.adjust(sku_id, DEFAULT_LOCATION_ID, 2, "key-in", "采购入库")

        with pytest.raises(InventoryValidationError):
            await service.adjust(sku_id, DEFAULT_LOCATION_ID, -3, "key-over", "超卖出库")

        availability = await service.get_availability([sku_id])
        assert availability[sku_id].available_quantity == 2
        assert await _movements(migrated_mysql_database, sku_id) == [(2, 2, "key-in")]

        # 非法调整量同样不写流水
        with pytest.raises(InventoryValidationError):
            await service.adjust(sku_id, DEFAULT_LOCATION_ID, 0, "key-zero", "零调整")
        with pytest.raises(InventoryValidationError):
            await service.adjust(sku_id, DEFAULT_LOCATION_ID, 1.5, "key-float", "浮点调整")

        # 没有余额行的 SKU 需要先建档
        fresh_sku = await _create_sku(migrated_mysql_database, code="P-2-000")
        with pytest.raises(InventoryNotFoundError):
            await service.adjust(fresh_sku, DEFAULT_LOCATION_ID, 1, "key-nobalance", "未建档")
        with pytest.raises(InventoryNotFoundError):
            await service.adjust(sku_id, "no-such-location", 1, "key-noloc", "库位不存在")

    asyncio.run(scenario())


def test_concurrent_outbound_never_oversells(migrated_mysql_database: str):
    """并发出库总量不能超过现有库存。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))
        await service.create_balance(sku_id)
        await service.adjust(sku_id, DEFAULT_LOCATION_ID, 8, "key-seed", "采购入库")

        results = await asyncio.gather(
            service.adjust(sku_id, DEFAULT_LOCATION_ID, -5, "key-a", "并发出库 A"),
            service.adjust(sku_id, DEFAULT_LOCATION_ID, -5, "key-b", "并发出库 B"),
            return_exceptions=True,
        )
        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, Exception)]
        assert len(successes) == 1
        assert isinstance(failures[0], InventoryValidationError)

        availability = await service.get_availability([sku_id])
        assert availability[sku_id].available_quantity == 3
        assert availability[sku_id].available_quantity >= 0
        # 只有一次出库被记录
        assert [item[0] for item in await _movements(migrated_mysql_database, sku_id)] == [8, -5]

    asyncio.run(scenario())


def test_idempotency_key_replays_once_and_conflicts_on_other_payloads(
    migrated_mysql_database: str,
):
    """同键同载荷只记一次；同键不同 SKU 或调整量必须报冲突。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        other_sku = await _create_sku(migrated_mysql_database, code="P-2-000")
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))
        await service.create_balance(sku_id)
        await service.create_balance(other_sku)

        first = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")
        replay = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")
        assert replay.on_hand == first.on_hand == 5
        assert await _movements(migrated_mysql_database, sku_id) == [(5, 2, "key-idem")]

        # 同键不同调整量
        with pytest.raises(InventoryConflictError):
            await service.adjust(sku_id, DEFAULT_LOCATION_ID, 7, "key-idem", "采购入库")
        # 同键不同 SKU
        with pytest.raises(InventoryConflictError):
            await service.adjust(other_sku, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")

        # 冲突不改动任何余额
        availability = await service.get_availability([sku_id, other_sku])
        assert availability[sku_id].available_quantity == 5
        assert availability[other_sku].available_quantity == 0
        assert await _movements(migrated_mysql_database, other_sku) == []

    asyncio.run(scenario())


def test_idempotent_replay_is_marked_and_never_reapplies(migrated_mysql_database: str):
    """重放同一幂等键不再加减，并明确标记为重复请求而不是原结果。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))
        await service.create_balance(sku_id)

        first = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")
        assert first.replayed is False
        assert first.on_hand == 5

        # 期间发生了别的调整，重放不能假装自己还是当初那次的结果
        await service.adjust(sku_id, DEFAULT_LOCATION_ID, -2, "key-out", "订单出库")

        replay = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")
        assert replay.replayed is True
        assert replay.on_hand == 3  # 当前余额，而不是把入库再加一次得到 8
        assert replay.available == 3
        assert [item[0] for item in await _movements(migrated_mysql_database, sku_id)] == [5, -2]

        # 紧接着再重放一次，结果稳定
        again = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-idem", "采购入库")
        assert again.replayed is True
        assert again.on_hand == 3
        assert [item[0] for item in await _movements(migrated_mysql_database, sku_id)] == [5, -2]

    asyncio.run(scenario())


# ---------- 可售查询 ----------


def test_availability_distinguishes_unknown_zero_and_aggregates_locations(
    migrated_mysql_database: str,
):
    """无余额行为 unknown，零余额为 out_of_stock，多库位数量正确汇总。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        unknown_sku = await _create_sku(migrated_mysql_database, code="P-2-000")
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))

        await service.create_balance(sku_id)
        availability = await service.get_availability([sku_id, unknown_sku])

        assert availability[sku_id].status == "out_of_stock"
        assert availability[sku_id].available_quantity == 0
        assert availability[unknown_sku].status == "unknown"
        assert availability[unknown_sku].available_quantity is None
        assert availability[unknown_sku].version is None

        second_location = await _location(migrated_mysql_database, "warehouse-2", "二号仓")
        await service.create_balance(sku_id, second_location)
        await service.adjust(sku_id, DEFAULT_LOCATION_ID, 5, "key-l1", "入库一号仓")
        await service.adjust(sku_id, second_location, 7, "key-l2", "入库二号仓")

        aggregated = (await service.get_availability([sku_id]))[sku_id]
        assert aggregated.status == "available"
        assert aggregated.available_quantity == 12
        assert aggregated.version == 2
        assert len(aggregated.locations) == 2
        assert {item.available for item in aggregated.locations} == {5, 7}

    asyncio.run(scenario())


def test_replay_marker_belongs_to_adjustment_not_to_availability(
    migrated_mysql_database: str,
):
    """replayed 只标注调整结果；可售查询的库位明细不应带该字段。"""

    async def scenario():
        sku_id = await _create_sku(migrated_mysql_database)
        factory = create_session_factory(migrated_mysql_database)
        service = InventoryService(factory, CatalogSKUReader(factory))
        await service.create_balance(sku_id)

        adjusted = await service.adjust(sku_id, DEFAULT_LOCATION_ID, 3, "key-in", "入库")
        assert set(adjusted.model_dump()) == {
            "sku_id",
            "location_id",
            "on_hand",
            "reserved",
            "available",
            "version",
            "replayed",
        }

        availability = (await service.get_availability([sku_id]))[sku_id]
        for balance in availability.locations:
            assert set(balance.model_dump()) == {
                "sku_id",
                "location_id",
                "on_hand",
                "reserved",
                "available",
                "version",
            }, "可售查询的库位明细不应包含 replayed"

    asyncio.run(scenario())


def test_availability_raises_dependency_error_on_backend_failure(
    migrated_mysql_database: str,
):
    """数据库故障必须抛出依赖错误，不能被当成有货。"""

    async def scenario():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        broken_engine = create_async_engine(
            "mysql+asyncmy://nobody:nobody@127.0.0.1:1/nothing"
        )
        factory = async_sessionmaker(broken_engine, expire_on_commit=False)
        broken = InventoryService(factory, CatalogSKUReader(factory))
        with pytest.raises(InventoryDependencyError):
            await broken.get_availability(["any-sku"])
        await broken_engine.dispose()

    asyncio.run(scenario())


def test_default_location_is_seeded_exactly_once(migrated_mysql_database: str):
    """默认库位由迁移写入，且只有一行。"""

    async def scenario():
        from ecom.modules.inventory.models import StockLocation

        async with create_session_factory(migrated_mysql_database)() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(StockLocation)
                .where(StockLocation.code == "default")
            )
        assert count == 1

    asyncio.run(scenario())
