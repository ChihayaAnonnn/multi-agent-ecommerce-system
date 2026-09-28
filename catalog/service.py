"""目录领域服务：类目、商品与 SKU 的受控写入。

规则要点：
- 每个写入方法自管一个事务；数据库唯一约束处理并发，冲突转换为领域异常。
- 只有草稿商品可以增删购买规格，避免已上架商品的 SKU 变成不完整组合。
- 上架要求至少一个可售 SKU、每个 SKU 选项组合完整，且每个 SKU 都有现行价格。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog.models import (
    Category,
    CategoryAttribute,
    OptionValue,
    Product,
    ProductMedia,
    ProductOption,
    SKU,
    SKUOptionValue,
)
from catalog.schemas import (
    AttributeInput,
    AttributeView,
    CategoryInput,
    CategoryRefView,
    CategoryView,
    MediaInput,
    MediaView,
    OptionDetailView,
    OptionInput,
    OptionValueInput,
    OptionValueView,
    OptionView,
    ProductDetailView,
    ProductInput,
    ProductPage,
    ProductSummaryView,
    ProductView,
    SelectedOptionValueView,
    SKUDetailView,
    SKUInput,
    SKUView,
)
from db.base import new_id

# 单页上限，防止一次拉取过多商品。
MAX_PAGE_SIZE = 100


class CatalogError(Exception):
    """目录领域异常基类。"""


class CatalogNotFoundError(CatalogError):
    """引用的实体不存在。"""


class CatalogConflictError(CatalogError):
    """与现有状态冲突：重复编码、版本过期、状态不允许。"""


class CatalogValidationError(CatalogError):
    """输入违反业务规则。"""


class PriceReader(Protocol):
    """只读价格查询边界。

    目录模块不读写价格表，只通过该协议确认可售 SKU 是否具有现行价格。
    实现是异步的：真实实现（pricing 模块）需要查询 MySQL。
    """

    async def has_current_prices(self, sku_ids: list[str]) -> bool: ...


def compute_option_signature(pairs: Sequence[tuple[str, str]]) -> str:
    """按选项 ID 排序后拼接 `option_id=value_id`，取 SHA-256。

    使用固定分隔符，避免不同组合拼出同一原文；无选项商品取固定值 `default`。
    """
    if not pairs:
        return "default"
    encoded = "|".join(
        f"{option_id}={value_id}" for option_id, value_id in sorted(pairs)
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _like_pattern(text: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_cursor(created_at: datetime, product_id: str) -> str:
    """把稳定的 (created_at, id) 编码为不透明游标。"""
    raw = f"{created_at.isoformat()}|{product_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    """解析游标；格式非法时给出确定的领域错误。"""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp, product_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), product_id
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise CatalogValidationError("无效的分页游标") from exc


def _validate_attribute_value(code: str, value: Any, value_type: str) -> None:
    """校验属性值与类目定义的 value_type 匹配。"""
    if value_type == "boolean":
        matched = isinstance(value, bool)
    elif value_type == "number":
        matched = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:  # text
        matched = isinstance(value, str)

    if not matched:
        raise CatalogValidationError(
            f"属性 {code} 的类型应为 {value_type}，实际为 {type(value).__name__}"
        )


class CatalogService:
    """目录写入与读取服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        price_reader: PriceReader | None = None,
    ):
        self._session_factory = session_factory
        self._price_reader = price_reader

    # ---------- 类目 ----------

    async def create_category(self, data: CategoryInput) -> CategoryView:
        async with self._session_factory() as session:
            if data.parent_id is not None:
                await self._require_category(session, data.parent_id)
            category = Category(
                id=new_id(),
                parent_id=data.parent_id,
                code=data.code,
                name=data.name,
                sort_order=data.sort_order,
            )
            session.add(category)
            await self._commit(session, f"类目编码已存在: {data.code}")
            await session.refresh(category)
            return CategoryView.model_validate(category)

    async def set_category_parent(
        self, category_id: str, parent_id: str | None
    ) -> CategoryView:
        """变更类目父级；拒绝形成循环引用。"""
        async with self._session_factory() as session:
            category = await self._require_category(session, category_id)
            if parent_id is not None:
                if parent_id == category_id:
                    raise CatalogValidationError("类目不能以自身为父类目")
                await self._require_category(session, parent_id)
                await self._assert_no_cycle(session, category_id, parent_id)
            category.parent_id = parent_id
            await self._commit(session, f"类目父级更新冲突: {category_id}")
            await session.refresh(category)
            return CategoryView.model_validate(category)

    async def add_category_attribute(
        self, category_id: str, data: AttributeInput
    ) -> AttributeView:
        async with self._session_factory() as session:
            await self._require_category(session, category_id)
            attribute = CategoryAttribute(
                id=new_id(),
                category_id=category_id,
                code=data.code,
                name=data.name,
                value_type=data.value_type,
                unit=data.unit,
                is_comparable=data.is_comparable,
                is_filterable=data.is_filterable,
            )
            session.add(attribute)
            await self._commit(session, f"类目属性编码已存在: {data.code}")
            await session.refresh(attribute)
            return AttributeView.model_validate(attribute)

    # ---------- 商品 ----------

    async def create_product(self, data: ProductInput) -> ProductView:
        async with self._session_factory() as session:
            await self._require_category(session, data.category_id)
            await self._validate_attributes(session, data.category_id, data.attributes)
            product = Product(
                id=new_id(),
                category_id=data.category_id,
                product_code=data.product_code,
                name=data.name,
                brand=data.brand,
                description=data.description,
                attributes=data.attributes or None,
            )
            session.add(product)
            await self._commit(session, f"商品编码已存在: {data.product_code}")
            await session.refresh(product)
            return ProductView.model_validate(product)

    async def add_option(self, product_id: str, data: OptionInput) -> OptionView:
        async with self._session_factory() as session:
            product = await self._require_product(session, product_id)
            if product.status != "draft":
                raise CatalogConflictError(
                    "只有草稿状态的商品可以调整购买规格；请先将商品转回草稿"
                )
            option = ProductOption(
                id=new_id(),
                product_id=product_id,
                code=data.code,
                name=data.name,
                sort_order=data.sort_order,
            )
            session.add(option)
            await self._commit(session, f"商品选项编码或排序已存在: {data.code}")
            await session.refresh(option)
            return OptionView.model_validate(option)

    async def add_option_value(
        self, option_id: str, data: OptionValueInput
    ) -> OptionValueView:
        async with self._session_factory() as session:
            option = await session.get(ProductOption, option_id)
            if option is None:
                raise CatalogNotFoundError(f"商品选项不存在: {option_id}")
            value = OptionValue(
                id=new_id(),
                option_id=option_id,
                code=data.code,
                display_value=data.display_value,
                sort_order=data.sort_order,
            )
            session.add(value)
            await self._commit(session, f"选项值编码已存在: {data.code}")
            await session.refresh(value)
            return OptionValueView.model_validate(value)

    async def add_media(self, product_id: str, data: MediaInput) -> MediaView:
        async with self._session_factory() as session:
            await self._require_product(session, product_id)
            if data.sku_id is not None:
                sku = await session.get(SKU, data.sku_id)
                if sku is None:
                    raise CatalogNotFoundError(f"SKU 不存在: {data.sku_id}")
                if sku.product_id != product_id:
                    raise CatalogValidationError("媒体绑定的 SKU 不属于该商品")
            media = ProductMedia(
                id=new_id(),
                product_id=product_id,
                sku_id=data.sku_id,
                url=data.url,
                media_type=data.media_type,
                alt_text=data.alt_text,
                sort_order=data.sort_order,
            )
            session.add(media)
            await self._commit(session, f"媒体写入冲突: {data.url}")
            await session.refresh(media)
            return MediaView.model_validate(media)

    # ---------- SKU ----------

    async def create_sku(self, product_id: str, data: SKUInput) -> SKUView:
        async with self._session_factory() as session:
            product = await self._require_product(session, product_id)
            if product.status == "archived":
                raise CatalogConflictError("已归档商品不能新增 SKU")

            options = (
                await session.scalars(
                    select(ProductOption).where(ProductOption.product_id == product_id)
                )
            ).all()
            option_ids = {option.id for option in options}

            pairs = await self._resolve_option_values(
                session, data.option_value_ids, option_ids
            )
            selected_option_ids = [option_id for option_id, _ in pairs]
            if len(set(selected_option_ids)) != len(selected_option_ids):
                raise CatalogValidationError("同一购买规格只能选择一个值")
            if set(selected_option_ids) != option_ids:
                raise CatalogValidationError(
                    "SKU 必须为商品的每个购买规格各选择一个值"
                )

            sku = SKU(
                id=new_id(),
                product_id=product_id,
                sku_code=data.sku_code,
                option_signature=compute_option_signature(pairs),
            )
            session.add(sku)
            for option_id, value_id in pairs:
                session.add(
                    SKUOptionValue(sku_id=sku.id, option_id=option_id, value_id=value_id)
                )
            await self._commit(session, f"SKU 编码或规格组合已存在: {data.sku_code}")
            await session.refresh(sku)
            return SKUView.model_validate(sku)

    # ---------- 状态流转 ----------

    async def activate_product(
        self, product_id: str, expected_version: int
    ) -> ProductView:
        async with self._session_factory() as session:
            product = await self._require_product(session, product_id)
            if product.status == "archived":
                raise CatalogConflictError("已归档商品不能重新上架")

            sku_ids = list(
                await session.scalars(
                    select(SKU.id).where(
                        SKU.product_id == product_id, SKU.status == "active"
                    )
                )
            )
            if not sku_ids:
                raise CatalogConflictError("上架前商品至少需要一个可售 SKU")

            await self._assert_complete_combinations(session, product_id, sku_ids)

            if self._price_reader is None:
                raise CatalogConflictError(
                    "缺少价格读取器：无法确认 SKU 是否有现行价格，拒绝上架"
                )
            if not await self._price_reader.has_current_prices(sku_ids):
                raise CatalogConflictError("上架前每个可售 SKU 都必须具有现行 CNY 价格")

            return await self._transition(
                session, product, expected_version, "active"
            )

    async def archive_product(
        self, product_id: str, expected_version: int
    ) -> ProductView:
        """归档商品：停止发现，但保留商品、SKU 与关联记录，不物理删除。"""
        async with self._session_factory() as session:
            product = await self._require_product(session, product_id)
            return await self._transition(
                session, product, expected_version, "archived"
            )

    # ---------- 读取 ----------

    async def get_product(self, product_id: str) -> ProductDetailView:
        """商品详情，含真实 SKU 组合、购买规格、媒体与类目属性定义。

        已归档商品同样返回，由 `status` 显式区分；停用 SKU 也会出现在 `skus` 中，
        调用方按 `status == "active"` 判断可售候选。
        """
        async with self._session_factory() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise CatalogNotFoundError(f"商品不存在: {product_id}")

            category = await self._require_category(session, product.category_id)
            attributes = list(
                await session.scalars(
                    select(CategoryAttribute)
                    .where(CategoryAttribute.category_id == product.category_id)
                    .order_by(CategoryAttribute.code)
                )
            )
            options = list(
                await session.scalars(
                    select(ProductOption)
                    .where(ProductOption.product_id == product_id)
                    .order_by(ProductOption.sort_order, ProductOption.id)
                )
            )
            option_values = await self._load_option_values(session, [o.id for o in options])
            skus = list(
                await session.scalars(
                    select(SKU)
                    .where(SKU.product_id == product_id)
                    .order_by(SKU.sku_code)
                )
            )
            selected = await self._load_selected_values(session, [s.id for s in skus])
            media = list(
                await session.scalars(
                    select(ProductMedia)
                    .where(ProductMedia.product_id == product_id)
                    .order_by(ProductMedia.sort_order, ProductMedia.id)
                )
            )

            return ProductDetailView(
                **ProductView.model_validate(product).model_dump(),
                category=CategoryRefView.model_validate(category),
                category_attributes=[
                    AttributeView.model_validate(item) for item in attributes
                ],
                options=[
                    OptionDetailView(
                        **OptionView.model_validate(option).model_dump(),
                        values=[
                            OptionValueView.model_validate(value)
                            for value in option_values.get(option.id, [])
                        ],
                    )
                    for option in options
                ],
                skus=[
                    SKUDetailView(
                        **SKUView.model_validate(sku).model_dump(),
                        selected_values=selected.get(sku.id, []),
                    )
                    for sku in skus
                ],
                media=[MediaView.model_validate(item) for item in media],
            )

    async def get_sku(self, sku_id: str) -> SKUDetailView:
        """单个 SKU 及其确定的选项组合；停用状态显式返回。"""
        async with self._session_factory() as session:
            sku = await session.get(SKU, sku_id)
            if sku is None:
                raise CatalogNotFoundError(f"SKU 不存在: {sku_id}")
            selected = await self._load_selected_values(session, [sku.id])
            return SKUDetailView(
                **SKUView.model_validate(sku).model_dump(),
                selected_values=selected.get(sku.id, []),
            )

    async def search(
        self,
        category_id: str | None = None,
        text: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProductPage:
        """按类目与文本搜索在售商品。

        文本只匹配商品名称与商品编码；排序固定为 `(created_at, id)` 倒序，
        游标是不透明编码，不接受调用方指定排序字段。
        """
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise CatalogValidationError(f"limit 必须在 1 到 {MAX_PAGE_SIZE} 之间")

        async with self._session_factory() as session:
            if category_id is not None:
                await self._require_category(session, category_id)

            statement = (
                select(Product)
                .where(Product.status == "active")
                .order_by(Product.created_at.desc(), Product.id.desc())
                .limit(limit + 1)
            )
            if category_id is not None:
                statement = statement.where(Product.category_id == category_id)
            if text:
                pattern = _like_pattern(text)
                statement = statement.where(
                    Product.name.like(pattern, escape="\\")
                    | Product.product_code.like(pattern, escape="\\")
                )
            if cursor is not None:
                created_at, product_id = _decode_cursor(cursor)
                statement = statement.where(
                    tuple_(Product.created_at, Product.id) < (created_at, product_id)
                )

            rows = list(await session.scalars(statement))

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            _encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        )
        return ProductPage(
            items=[ProductSummaryView.model_validate(item) for item in items],
            next_cursor=next_cursor,
        )

    # ---------- 内部工具 ----------

    @staticmethod
    async def _load_option_values(
        session: AsyncSession, option_ids: list[str]
    ) -> dict[str, list[OptionValue]]:
        if not option_ids:
            return {}
        rows = await session.scalars(
            select(OptionValue)
            .where(OptionValue.option_id.in_(option_ids))
            .order_by(OptionValue.sort_order, OptionValue.id)
        )
        grouped: dict[str, list[OptionValue]] = {}
        for row in rows:
            grouped.setdefault(row.option_id, []).append(row)
        return grouped

    @staticmethod
    async def _load_selected_values(
        session: AsyncSession, sku_ids: list[str]
    ) -> dict[str, list[SelectedOptionValueView]]:
        """一次查询取回所有 SKU 的选项组合，避免按 SKU 逐条加载。"""
        if not sku_ids:
            return {}
        rows = await session.execute(
            select(
                SKUOptionValue.sku_id,
                ProductOption.id,
                ProductOption.code,
                ProductOption.name,
                OptionValue.id,
                OptionValue.code,
                OptionValue.display_value,
            )
            .join(ProductOption, ProductOption.id == SKUOptionValue.option_id)
            .join(OptionValue, OptionValue.id == SKUOptionValue.value_id)
            .where(SKUOptionValue.sku_id.in_(sku_ids))
            .order_by(ProductOption.sort_order, ProductOption.id)
        )
        grouped: dict[str, list[SelectedOptionValueView]] = {}
        for (
            sku_id,
            option_id,
            option_code,
            option_name,
            value_id,
            value_code,
            display_value,
        ) in rows:
            grouped.setdefault(sku_id, []).append(
                SelectedOptionValueView(
                    option_id=option_id,
                    option_code=option_code,
                    option_name=option_name,
                    value_id=value_id,
                    value_code=value_code,
                    display_value=display_value,
                )
            )
        return grouped

    async def _transition(
        self,
        session: AsyncSession,
        product: Product,
        expected_version: int,
        status: str,
    ) -> ProductView:
        """按期望版本号做条件更新，避免并发覆盖。"""
        result = await session.execute(
            update(Product)
            .where(Product.id == product.id, Product.version == expected_version)
            .values(status=status, version=Product.version + 1)
        )
        if result.rowcount != 1:
            await session.rollback()
            raise CatalogConflictError(
                f"商品版本已变化：期望 {expected_version}，请重新读取后再操作"
            )
        await session.commit()
        await session.refresh(product)
        return ProductView.model_validate(product)

    async def _assert_complete_combinations(
        self, session: AsyncSession, product_id: str, sku_ids: list[str]
    ) -> None:
        """每个可售 SKU 都必须覆盖商品当前的全部购买规格。"""
        option_count = await session.scalar(
            select(func.count())
            .select_from(ProductOption)
            .where(ProductOption.product_id == product_id)
        )
        if not option_count:
            return
        incomplete = await session.scalar(
            select(func.count())
            .select_from(
                select(SKUOptionValue.sku_id)
                .where(SKUOptionValue.sku_id.in_(sku_ids))
                .group_by(SKUOptionValue.sku_id)
                .having(func.count() != option_count)
                .subquery()
            )
        )
        if incomplete:
            raise CatalogConflictError(
                "存在购买规格不完整的 SKU（通常是草稿期新增了规格），不能上架"
            )

    async def _resolve_option_values(
        self,
        session: AsyncSession,
        value_ids: list[str],
        option_ids: set[str],
    ) -> list[tuple[str, str]]:
        if not value_ids:
            return []
        if len(set(value_ids)) != len(value_ids):
            raise CatalogValidationError("同一 SKU 不能重复选择同一个选项值")
        rows = (
            await session.scalars(select(OptionValue).where(OptionValue.id.in_(value_ids)))
        ).all()
        if len(rows) != len(value_ids):
            raise CatalogNotFoundError("存在无效的选项值")
        pairs: list[tuple[str, str]] = []
        for row in rows:
            if row.option_id not in option_ids:
                raise CatalogValidationError("选项值不属于该商品的购买规格")
            pairs.append((row.option_id, row.id))
        return pairs

    async def _validate_attributes(
        self, session: AsyncSession, category_id: str, attributes: dict[str, Any]
    ) -> None:
        if not attributes:
            return
        definitions = {
            row.code: row
            for row in await session.scalars(
                select(CategoryAttribute).where(
                    CategoryAttribute.category_id == category_id
                )
            )
        }
        for code, value in attributes.items():
            definition = definitions.get(code)
            if definition is None:
                raise CatalogValidationError(f"属性 {code} 未在类目中定义")
            _validate_attribute_value(code, value, definition.value_type)

    @staticmethod
    async def _assert_no_cycle(
        session: AsyncSession, category_id: str, parent_id: str
    ) -> None:
        """沿父链上溯；回到自身说明会形成循环引用。"""
        current: str | None = parent_id
        seen: set[str] = set()
        while current is not None:
            if current == category_id:
                raise CatalogValidationError("类目父级不能指向自身的后代，会形成循环引用")
            if current in seen:
                raise CatalogValidationError("现有类目层级中已存在循环引用")
            seen.add(current)
            current = await session.scalar(
                select(Category.parent_id).where(Category.id == current)
            )

    @staticmethod
    async def _require_category(session: AsyncSession, category_id: str) -> Category:
        category = await session.get(Category, category_id)
        if category is None:
            raise CatalogNotFoundError(f"类目不存在: {category_id}")
        return category

    @staticmethod
    async def _require_product(session: AsyncSession, product_id: str) -> Product:
        product = await session.get(Product, product_id)
        if product is None:
            raise CatalogNotFoundError(f"商品不存在: {product_id}")
        return product

    @staticmethod
    async def _commit(session: AsyncSession, conflict_message: str) -> None:
        """提交事务；唯一约束冲突转换为领域异常。"""
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise CatalogConflictError(conflict_message) from exc
