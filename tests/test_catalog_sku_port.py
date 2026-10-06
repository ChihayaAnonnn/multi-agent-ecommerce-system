"""SKU 存在性端口的注入、会话复用与模块异常边界。"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, event

from ecom.infra.db import create_session_factory
from ecom.modules.catalog.models import SKU
from ecom.modules.catalog.ports import PriceReader, SKUExistenceReader
from ecom.modules.catalog.schemas import CategoryInput, ProductInput, SKUInput
from ecom.modules.catalog.service import (
    CatalogNotFoundError,
    CatalogService,
    CatalogSKUReader,
)
from ecom.modules.catalog.service import (
    PriceReader as LegacyPriceReader,
)
from ecom.modules.inventory.service import InventoryNotFoundError, InventoryService
from ecom.modules.pricing.service import PricingNotFoundError, PricingService
from tests.test_layering import (
    run_packaged_imports,
    write_packaged_config,
    write_packaged_tree,
)


@pytest.fixture
def factory_stub():
    @asynccontextmanager
    async def factory():
        yield object()

    return factory


class MissingSKUReader:
    def __init__(self):
        self.calls = []

    async def require_sku_exists(self, db, sku_id: str) -> None:
        self.calls.append((db, sku_id))
        raise CatalogNotFoundError(f"SKU 不存在: {sku_id}")


@pytest.mark.parametrize(
    "service_type,error_type",
    [(PricingService, PricingNotFoundError),
     (InventoryService, InventoryNotFoundError)],
)
def test_missing_sku_maps_to_module_not_found(
    factory_stub, service_type, error_type,
):
    async def scenario():
        reader = MissingSKUReader()
        service = service_type(factory_stub, sku_reader=reader)
        with pytest.raises(error_type, match="SKU 不存在: missing") as caught:
            if service_type is PricingService:
                await service.set_price("missing", 100, None, "测试")
            else:
                await service.create_balance("missing")
        assert isinstance(caught.value.__cause__, CatalogNotFoundError)
        assert len(reader.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("service_type", [PricingService, InventoryService])
def test_existence_check_reuses_callers_session(service_type):
    async def scenario():
        caller_session = object()
        sessions = []

        @asynccontextmanager
        async def factory():
            sessions.append(caller_session)
            yield caller_session

        reader = MissingSKUReader()
        service = service_type(factory, sku_reader=reader)
        with pytest.raises((PricingNotFoundError, InventoryNotFoundError)):
            if service_type is PricingService:
                await service.set_price("missing", 100, None, "测试")
            else:
                await service.create_balance("missing")
        assert sessions == [caller_session]
        assert reader.calls == [(caller_session, "missing")]

    asyncio.run(scenario())


def test_catalog_reader_constructs_without_pricing(factory_stub):
    reader = CatalogSKUReader(factory_stub)
    assert isinstance(reader, CatalogSKUReader)


@pytest.mark.parametrize("exists", [False, True])
def test_catalog_reader_only_reads_callers_session(factory_stub, exists):
    async def scenario():
        calls = []

        class SessionStub:
            async def get(self, model, sku_id):
                calls.append((model, sku_id))
                return object() if exists else None

        reader: SKUExistenceReader = CatalogSKUReader(factory_stub)
        if exists:
            assert await reader.require_sku_exists(SessionStub(), "sku") is None
        else:
            with pytest.raises(CatalogNotFoundError, match="SKU 不存在: sku"):
                await reader.require_sku_exists(SessionStub(), "sku")
        assert calls == [(SKU, "sku")]

    asyncio.run(scenario())


@pytest.mark.parametrize("service_type", [PricingService, InventoryService])
def test_sku_reader_is_required(factory_stub, service_type):
    with pytest.raises(TypeError, match="sku_reader"):
        service_type(factory_stub)


def test_price_reader_compatibility_alias_is_same_object():
    assert LegacyPriceReader is PriceReader


def test_catalog_reader_observes_uncommitted_caller_transaction(
    migrated_mysql_database,
):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        reader = CatalogSKUReader(factory)
        catalog = CatalogService(factory)
        category = await catalog.create_category(
            CategoryInput(code="port", name="测试"),
        )
        product = await catalog.create_product(
            ProductInput(category_id=category.id, product_code="port", name="测试"),
        )
        sku = await catalog.create_sku(product.id, SKUInput(sku_code="port"))
        statements = []

        def record_statement(connection, cursor, statement, parameters, context, many):
            statements.append(statement)

        engine = factory.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            async with factory() as db:
                assert await reader.require_sku_exists(db, sku.id) is None
                await db.execute(delete(SKU).where(SKU.id == sku.id))
                with pytest.raises(CatalogNotFoundError):
                    await reader.require_sku_exists(db, sku.id)
                assert db.in_transaction()
                await db.rollback()
            async with factory() as db:
                assert await db.get(SKU, sku.id) is not None
            assert all(
                "FOR UPDATE" not in statement.upper() for statement in statements
            )
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)

    asyncio.run(scenario())


@pytest.mark.parametrize("consumer", ["ecom.modules.pricing", "ecom.modules.inventory"])
@pytest.mark.parametrize("via_catalog_service", [False, True])
def test_stage_enforces_catalog_model_ownership(
    tmp_path, consumer, via_catalog_service,
):
    write_packaged_tree(tmp_path)
    (tmp_path / "ecom" / "modules" / "catalog" / "service.py").write_text(
        "import ecom.modules.catalog.models\n", encoding="utf-8",
    )
    dependency = ("ecom.modules.catalog.service" if via_catalog_service
                  else "ecom.modules.catalog.models")
    (tmp_path.joinpath(*consumer.split(".")) / "service.py").write_text(
        f"import {dependency}\n", encoding="utf-8",
    )
    config = write_packaged_config(tmp_path)
    if via_catalog_service:
        run_packaged_imports(config, tmp_path)
    else:
        with pytest.raises(AssertionError, match="BROKEN"):
            run_packaged_imports(config, tmp_path)
