"""目录领域：迁移约束、写入规则与 SKU 组合校验的 MySQL 测试。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from catalog.models import Product, SKU
from catalog.schemas import (
    AttributeInput,
    CategoryInput,
    MediaInput,
    OptionInput,
    OptionValueInput,
    ProductInput,
    SKUInput,
)
from catalog.service import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogService,
    CatalogValidationError,
    compute_option_signature,
)
from db.session import create_session_factory
from tests.conftest import downgrade_to_base, read_schema, upgrade_to

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


class StubPriceReader:
    """Task 2 的价格读取桩；Task 4 会注入真实 pricing 服务。"""

    def __init__(self, has_prices: bool = True):
        self.has_prices = has_prices
        self.checked: list[list[str]] = []

    async def has_current_prices(self, sku_ids: list[str]) -> bool:
        self.checked.append(list(sku_ids))
        return self.has_prices


def test_catalog_migration_constraints(migrated_mysql_database: str):
    """0001 迁移建立 8 张目录表、关键唯一约束，且商品表不存价格与库存。"""
    # 后续迁移会新增其他领域的表，这里固定在目录基线版本上断言。
    downgrade_to_base(migrated_mysql_database)
    upgrade_to(migrated_mysql_database, "0001_catalog")
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


# ---------- 测试辅助 ----------


async def _scalar(database_url: str, statement):
    async with create_session_factory(database_url)() as session:
        return await session.scalar(statement)


async def _create_category(service: CatalogService, code: str = "c-apple", name: str = "手机"):
    return await service.create_category(CategoryInput(code=code, name=name))


async def _create_product(
    service: CatalogService,
    category_id: str,
    code: str = "P-1",
    attributes: dict | None = None,
):
    return await service.create_product(
        ProductInput(
            category_id=category_id,
            product_code=code,
            name="测试商品",
            attributes=attributes or {},
        )
    )


async def _product_with_options(service: CatalogService, category_id: str, code: str = "P-2"):
    """商品 + 颜色/容量两个选项，以及各自的值。"""
    product = await _create_product(service, category_id, code=code)
    color = await service.add_option(
        product.id, OptionInput(code="color", name="颜色", sort_order=1)
    )
    storage = await service.add_option(
        product.id, OptionInput(code="storage", name="容量", sort_order=2)
    )
    black = await service.add_option_value(
        color.id, OptionValueInput(code="black", display_value="黑色")
    )
    white = await service.add_option_value(
        color.id, OptionValueInput(code="white", display_value="白色")
    )
    g128 = await service.add_option_value(
        storage.id, OptionValueInput(code="128", display_value="128G")
    )
    g256 = await service.add_option_value(
        storage.id, OptionValueInput(code="256", display_value="256G")
    )
    return product, color, storage, black, white, g128, g256


# ---------- 写入规则 ----------


def test_product_without_options_uses_default_signature(migrated_mysql_database: str):
    """无规格商品使用一个默认 SKU，签名为固定值 default。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)
        product = await _create_product(service, category.id)

        assert product.status == "draft"
        sku = await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))

        assert sku.option_signature == "default"
        assert sku.status == "active"
        assert await _scalar(
            migrated_mysql_database,
            select(func.count()).select_from(SKU).where(SKU.product_id == product.id),
        ) == 1

    asyncio.run(scenario())


def test_only_created_combinations_exist(migrated_mysql_database: str):
    """只存在实际创建的组合，不生成选项的笛卡尔积。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)
        product, color, storage, black, white, g128, g256 = await _product_with_options(
            service, category.id
        )

        first = await service.create_sku(
            product.id, SKUInput(sku_code="P-2-B-128", option_value_ids=[black.id, g128.id])
        )
        second = await service.create_sku(
            # 故意打乱顺序：签名必须与选项 ID 排序无关
            product.id, SKUInput(sku_code="P-2-W-256", option_value_ids=[g256.id, white.id])
        )

        assert first.option_signature == compute_option_signature(
            [(color.id, black.id), (storage.id, g128.id)]
        )
        assert first.option_signature != second.option_signature
        assert await _scalar(
            migrated_mysql_database,
            select(func.count()).select_from(SKU).where(SKU.product_id == product.id),
        ) == 2

    asyncio.run(scenario())


def test_duplicate_codes_and_combinations_are_rejected(migrated_mysql_database: str):
    """重复编码、重复排序与重复规格组合都必须失败。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)

        with pytest.raises(CatalogConflictError):
            await _create_category(service)

        product = await _create_product(service, category.id)
        with pytest.raises(CatalogConflictError):
            await _create_product(service, category.id)

        option = await service.add_option(
            product.id, OptionInput(code="color", name="颜色", sort_order=1)
        )
        with pytest.raises(CatalogConflictError):
            await service.add_option(
                product.id, OptionInput(code="size", name="容量", sort_order=1)
            )

        black = await service.add_option_value(
            option.id, OptionValueInput(code="black", display_value="黑色")
        )
        with pytest.raises(CatalogConflictError):
            await service.add_option_value(
                option.id, OptionValueInput(code="black", display_value="黑色（重复）")
            )

        sku = await service.create_sku(
            product.id, SKUInput(sku_code="P-1-A", option_value_ids=[black.id])
        )
        with pytest.raises(CatalogConflictError):
            await service.create_sku(
                product.id, SKUInput(sku_code="P-1-A", option_value_ids=[black.id])
            )
        # 相同规格组合、不同编码：仍须由唯一约束拒绝
        with pytest.raises(CatalogConflictError):
            await service.create_sku(
                product.id, SKUInput(sku_code="P-1-B", option_value_ids=[black.id])
            )
        assert len(sku.option_signature) == 64

    asyncio.run(scenario())


def test_sku_rejects_values_outside_its_product_options(migrated_mysql_database: str):
    """选项值缺失、重复选择同一规格、或属于其他商品时都不能建 SKU。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)
        product, color, storage, black, white, g128, _ = await _product_with_options(
            service, category.id
        )

        # 缺少其中一个规格的值
        with pytest.raises(CatalogValidationError):
            await service.create_sku(
                product.id, SKUInput(sku_code="P-2-missing", option_value_ids=[black.id])
            )

        # 同一个规格选了两次
        with pytest.raises(CatalogValidationError):
            await service.create_sku(
                product.id, SKUInput(sku_code="P-2-dup", option_value_ids=[black.id, white.id])
            )

        # 值来自其他商品
        other_product = await _create_product(service, category.id, code="P-3")
        other_option = await service.add_option(
            other_product.id, OptionInput(code="color", name="颜色", sort_order=1)
        )
        other_value = await service.add_option_value(
            other_option.id, OptionValueInput(code="red", display_value="红色")
        )
        with pytest.raises(CatalogValidationError):
            await service.create_sku(
                product.id,
                SKUInput(sku_code="P-2-cross", option_value_ids=[other_value.id, g128.id]),
            )

        # 不存在的值
        with pytest.raises(CatalogNotFoundError):
            await service.create_sku(
                product.id, SKUInput(sku_code="P-2-ghost", option_value_ids=["no-such-value"])
            )

        # 有规格的商品不能建“无规格”SKU
        with pytest.raises(CatalogValidationError):
            await service.create_sku(product.id, SKUInput(sku_code="P-2-empty"))

        assert storage.id  # 该选项仍未被任何 SKU 使用

    asyncio.run(scenario())


def test_adding_option_to_active_product_is_rejected(migrated_mysql_database: str):
    """已上架商品不能追加购买规格；但可以新增规格组合（新 SKU）。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)

        # 无规格商品：上架后再追加规格必须拒绝
        product = await _create_product(service, category.id)
        await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))
        active = await service.activate_product(product.id, expected_version=product.version)
        assert active.status == "active"

        with pytest.raises(CatalogConflictError):
            await service.add_option(
                product.id, OptionInput(code="color", name="颜色", sort_order=1)
            )

        # 无规格商品只有一个组合，再建第二个 SKU 必然冲突
        with pytest.raises(CatalogConflictError):
            await service.create_sku(product.id, SKUInput(sku_code="P-1-001"))

        # 有规格商品：上架后新增一个尚未使用的组合是允许的
        other, color, storage, black, white, g128, g256 = await _product_with_options(
            service, category.id, code="P-2"
        )
        await service.create_sku(
            other.id, SKUInput(sku_code="P-2-B-128", option_value_ids=[black.id, g128.id])
        )
        other_active = await service.activate_product(
            other.id, expected_version=other.version
        )
        assert other_active.status == "active"

        new_sku = await service.create_sku(
            other.id, SKUInput(sku_code="P-2-W-256", option_value_ids=[white.id, g256.id])
        )
        assert new_sku.status == "active"

    asyncio.run(scenario())


def test_activation_refuses_incomplete_combinations(migrated_mysql_database: str):
    """草稿期新增规格会让旧 SKU 组合不完整，上架必须被拒绝。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        product = await _create_product(service, category.id)
        color = await service.add_option(
            product.id, OptionInput(code="color", name="颜色", sort_order=1)
        )
        black = await service.add_option_value(
            color.id, OptionValueInput(code="black", display_value="黑色")
        )
        await service.create_sku(product.id, SKUInput(sku_code="P-1-B", option_value_ids=[black.id]))

        # 草稿期仍可新增规格：此时既有 SKU 只剩颜色，没有容量
        storage = await service.add_option(
            product.id, OptionInput(code="storage", name="容量", sort_order=2)
        )
        await service.add_option_value(
            storage.id, OptionValueInput(code="128", display_value="128G")
        )

        with pytest.raises(CatalogConflictError):
            await service.activate_product(product.id, expected_version=product.version)

    asyncio.run(scenario())


# ---------- 上架与归档 ----------


def test_activation_requires_sku_and_price(migrated_mysql_database: str):
    """上架需要至少一个 SKU、可用的价格读取器，以及每个 SKU 的现行价格。"""

    async def scenario():
        category = await _create_category(
            CatalogService(create_session_factory(migrated_mysql_database))
        )

        # 没有 SKU
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        product = await _create_product(service, category.id)
        with pytest.raises(CatalogConflictError):
            await service.activate_product(product.id, expected_version=product.version)

        sku = await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))

        # 缺少价格读取器
        bare_service = CatalogService(create_session_factory(migrated_mysql_database))
        with pytest.raises(CatalogConflictError):
            await bare_service.activate_product(product.id, expected_version=product.version)

        # 读取器确认没有现行价格
        unpriced = StubPriceReader(has_prices=False)
        unpriced_service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=unpriced
        )
        with pytest.raises(CatalogConflictError):
            await unpriced_service.activate_product(
                product.id, expected_version=product.version
            )

        # 正常上架，并且确实校验了该 SKU
        reader = StubPriceReader()
        priced_service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=reader
        )
        active = await priced_service.activate_product(
            product.id, expected_version=product.version
        )
        assert active.status == "active"
        assert active.version == product.version + 1
        assert reader.checked == [[sku.id]]

    asyncio.run(scenario())


def test_activation_and_archive_reject_stale_version(migrated_mysql_database: str):
    """版本号不匹配时，上架与归档都必须失败。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        product = await _create_product(service, category.id)
        await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))

        with pytest.raises(CatalogConflictError):
            await service.activate_product(product.id, expected_version=product.version + 5)

        active = await service.activate_product(product.id, expected_version=product.version)
        assert active.version == 2

        # 归档用的是旧版本号
        with pytest.raises(CatalogConflictError):
            await service.archive_product(product.id, expected_version=1)

        archived = await service.archive_product(product.id, expected_version=active.version)
        assert archived.status == "archived"
        assert archived.version == 3

    asyncio.run(scenario())


def test_archive_keeps_product_and_sku_rows(migrated_mysql_database: str):
    """归档不物理删除商品与 SKU。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        product = await _create_product(service, category.id)
        sku = await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))
        active = await service.activate_product(product.id, expected_version=product.version)
        await service.archive_product(product.id, expected_version=active.version)

        assert await _scalar(
            migrated_mysql_database,
            select(func.count()).select_from(Product).where(Product.id == product.id),
        ) == 1
        assert await _scalar(
            migrated_mysql_database,
            select(func.count()).select_from(SKU).where(SKU.id == sku.id),
        ) == 1

    asyncio.run(scenario())


# ---------- 类目与属性 ----------


def test_category_parent_cycle_is_rejected(migrated_mysql_database: str):
    """父类目不能指向自身或自身的后代。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        root = await service.create_category(CategoryInput(code="root", name="数码"))
        child = await service.create_category(
            CategoryInput(code="child", name="手机", parent_id=root.id)
        )
        grandchild = await service.create_category(
            CategoryInput(code="grandchild", name="配件", parent_id=child.id)
        )

        with pytest.raises(CatalogValidationError):
            await service.set_category_parent(root.id, root.id)

        with pytest.raises(CatalogValidationError):
            await service.set_category_parent(root.id, grandchild.id)

        # 合法的移动：先断开，再挂到原来的后代下
        await service.set_category_parent(grandchild.id, None)
        moved = await service.set_category_parent(root.id, grandchild.id)
        assert moved.parent_id == grandchild.id

        with pytest.raises(CatalogNotFoundError):
            await service.set_category_parent(root.id, "no-such-category")

    asyncio.run(scenario())


def test_product_attributes_are_validated_against_category_definitions(
    migrated_mysql_database: str,
):
    """属性必须已定义且类型匹配，值保存在 products.attributes。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)
        await service.add_category_attribute(
            category.id,
            AttributeInput(
                code="screen_size", name="屏幕尺寸", value_type="number", unit="inch"
            ),
        )

        with pytest.raises(CatalogValidationError):
            await _create_product(service, category.id, attributes={"screen_size": "6.1"})

        with pytest.raises(CatalogValidationError):
            await _create_product(service, category.id, attributes={"not_defined": 1})

        product = await _create_product(
            service, category.id, attributes={"screen_size": 6.1}
        )
        assert product.attributes == {"screen_size": 6.1}

    asyncio.run(scenario())


def test_media_rejects_sku_from_another_product(migrated_mysql_database: str):
    """媒体绑定的 SKU 必须属于同一商品。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)
        first = await _create_product(service, category.id, code="P-1")
        second = await _create_product(service, category.id, code="P-2")
        sku = await service.create_sku(first.id, SKUInput(sku_code="P-1-000"))

        with pytest.raises(CatalogValidationError):
            await service.add_media(
                second.id, MediaInput(url="https://example.com/a.png", media_type="image", sku_id=sku.id)
            )

        media = await service.add_media(
            first.id, MediaInput(url="https://example.com/a.png", media_type="image", sku_id=sku.id)
        )
        assert media.product_id == first.id

        with pytest.raises(CatalogNotFoundError):
            await service.add_media(
                first.id, MediaInput(url="https://example.com/b.png", media_type="image", sku_id="ghost")
            )

    asyncio.run(scenario())


# ---------- 读取与搜索 ----------


async def _activate(service: CatalogService, product, sku_code: str, option_value_ids=None):
    """建一个 SKU 并上架，返回上架后的商品。"""
    await service.create_sku(
        product.id, SKUInput(sku_code=sku_code, option_value_ids=option_value_ids or [])
    )
    return await service.activate_product(product.id, expected_version=product.version)


def test_product_detail_returns_real_sku_combinations(migrated_mysql_database: str):
    """详情返回真实 SKU 组合、媒体与类目属性定义，不伪造笛卡尔积。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        await service.add_category_attribute(
            category.id, AttributeInput(code="color_family", name="色系", value_type="text")
        )
        product, color, storage, black, white, g128, g256 = await _product_with_options(
            service, category.id
        )
        await service.add_media(
            product.id, MediaInput(url="https://example.com/a.png", media_type="image")
        )
        product = await _activate(
            service, product, "P-2-B-128", [black.id, g128.id]
        )

        detail = await service.get_product(product.id)

        assert detail.status == "active"
        assert detail.category.code == "c-apple"
        assert [item.code for item in detail.category_attributes] == ["color_family"]
        assert len(detail.options) == 2
        assert [value.code for value in detail.options[0].values] == ["black", "white"]
        assert len(detail.media) == 1
        # 只存在实际创建的那一个组合
        assert len(detail.skus) == 1
        assert {(v.option_code, v.value_code) for v in detail.skus[0].selected_values} == {
            ("color", "black"),
            ("storage", "128"),
        }

    asyncio.run(scenario())


def test_single_reads_distinguish_inactive_sku_and_archived_product(
    migrated_mysql_database: str,
):
    """停用 SKU 与归档商品在单项读取中可辨别，不被静默隐藏。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        product = await _create_product(service, category.id)
        sku = await service.create_sku(product.id, SKUInput(sku_code="P-1-000"))

        # 让 SKU 停用后，单项读取仍能看到它的状态
        async with create_session_factory(migrated_mysql_database)() as session:
            stored = await session.get(SKU, sku.id)
            stored.status = "inactive"
            await session.commit()

        detail = await service.get_sku(sku.id)
        assert detail.status == "inactive"

        archived = await service.archive_product(product.id, expected_version=product.version)
        assert archived.status == "archived"

        # 归档商品在详情中仍可读取，并由 status 区分
        product_detail = await service.get_product(product.id)
        assert product_detail.status == "archived"
        assert [item.id for item in product_detail.skus] == [sku.id]

        with pytest.raises(CatalogNotFoundError):
            await service.get_product("no-such-product")
        with pytest.raises(CatalogNotFoundError):
            await service.get_sku("no-such-sku")

    asyncio.run(scenario())


def test_search_returns_only_active_products_with_stable_cursor(
    migrated_mysql_database: str,
):
    """搜索只返回在售商品，按游标翻页不重不漏。"""

    async def scenario():
        service = CatalogService(
            create_session_factory(migrated_mysql_database), price_reader=StubPriceReader()
        )
        category = await _create_category(service)
        other_category = await _create_category(service, code="c-other", name="配件")

        expected = []
        for index in range(5):
            product = await _create_product(
                service, category.id, code=f"P-{index}",
            )
            expected.append((await _activate(service, product, f"P-{index}-000")).id)

        # 草稿商品不进搜索
        await _create_product(service, category.id, code="P-draft")
        # 其他类目的在售商品
        other = await _create_product(service, other_category.id, code="P-other")
        await _activate(service, other, "P-other-000")

        first = await service.search(category_id=category.id, limit=2)
        assert len(first.items) == 2
        assert first.next_cursor is not None

        second = await service.search(category_id=category.id, limit=2, cursor=first.next_cursor)
        third = await service.search(category_id=category.id, limit=2, cursor=second.next_cursor)

        seen = [item.id for page in (first, second, third) for item in page.items]
        # 按 (created_at, id) 倒序翻页：不重、不漏、顺序稳定
        assert seen == list(reversed(expected))
        assert len(seen) == len(set(seen)) == 5
        assert third.next_cursor is None

        # 相同游标重复请求得到相同一页（稳定，不重不漏）
        again = await service.search(category_id=category.id, limit=2, cursor=first.next_cursor)
        assert [item.id for item in again.items] == [item.id for item in second.items]

        # 不带类目过滤时，其他类目的在售商品同样出现在结果里
        everything = await service.search(limit=100)
        assert other.id in {item.id for item in everything.items}
        assert len(everything.items) == 6

        # 类目过滤与文本过滤
        by_category = await service.search(category_id=other_category.id)
        assert [item.id for item in by_category.items] == [other.id]
        by_text = await service.search(text="P-3")
        assert [item.product_code for item in by_text.items] == ["P-3"]
        # LIKE 通配符按字面处理
        assert (await service.search(text="%")).items == []
        assert (await service.search(text="P_3")).items == []

    asyncio.run(scenario())


def test_search_rejects_oversized_limit_invalid_cursor_and_unknown_category(
    migrated_mysql_database: str,
):
    """limit 越界、游标非法、类目不存在都要给出确定错误。"""

    async def scenario():
        service = CatalogService(create_session_factory(migrated_mysql_database))
        category = await _create_category(service)

        with pytest.raises(CatalogValidationError):
            await service.search(limit=0)
        with pytest.raises(CatalogValidationError):
            await service.search(limit=101)
        with pytest.raises(CatalogValidationError):
            await service.search(cursor="not-a-cursor")
        with pytest.raises(CatalogNotFoundError):
            await service.search(category_id="no-such-category")

        assert (await service.search(limit=100)).items == []
        assert category.id  # 已存在的类目可正常搜索

    asyncio.run(scenario())
