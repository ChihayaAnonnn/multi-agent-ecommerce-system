"""目录表结构与迁移的 MySQL 约束测试。"""

from __future__ import annotations

from tests.conftest import downgrade_to_base, read_schema

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


def test_catalog_migration_constraints(migrated_mysql_database: str):
    """迁移后应存在 8 张目录表、关键唯一约束，且商品表不存价格与库存。"""
    schema = read_schema(migrated_mysql_database)

    domain_tables = schema["tables"] - {"alembic_version"}
    assert domain_tables == CATALOG_TABLES

    assert ("products", ("product_code",)) in schema["uniques"]
    assert ("skus", ("sku_code",)) in schema["uniques"]
    assert ("skus", ("option_signature", "product_id")) in schema["uniques"]

    product_columns = schema["columns"]["products"]
    assert "price" not in product_columns
    assert "stock" not in product_columns

    downgrade_to_base(migrated_mysql_database)
    assert read_schema(migrated_mysql_database)["tables"] - {"alembic_version"} == set()
