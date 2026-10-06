"""SKU 存在性读取必须在调用方未提交的事务内可见（真实 MySQL）。"""

import asyncio

import pytest
from sqlalchemy import delete, event

from ecom.infra.db import create_session_factory
from ecom.modules.catalog.models import SKU
from ecom.modules.catalog.schemas import CategoryInput, ProductInput, SKUInput
from ecom.modules.catalog.service import (
    CatalogNotFoundError,
    CatalogService,
    CatalogSKUReader,
)


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
