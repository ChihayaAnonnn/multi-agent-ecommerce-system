"""目录领域输入输出 DTO。

输入 DTO 只做形状与长度校验；业务规则（编码唯一、选项组合完整性、属性类型、
上架条件）由 catalog.service 在事务内校验。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from db.base import UtcDateTime

AttributeValueType = Literal["text", "number", "boolean"]
CategoryStatus = Literal["active", "inactive"]
ProductStatus = Literal["draft", "active", "archived"]
SKUStatus = Literal["active", "inactive"]


class _View(BaseModel):
    """视图 DTO 直接由 ORM 对象构造。"""

    model_config = ConfigDict(from_attributes=True)


# ---------- 类目 ----------


class CategoryInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = None
    sort_order: int = 0


class CategoryView(_View):
    id: str
    parent_id: str | None
    code: str
    name: str
    status: CategoryStatus
    sort_order: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AttributeInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    value_type: AttributeValueType
    unit: str | None = Field(default=None, max_length=32)
    is_comparable: bool = False
    is_filterable: bool = False


class AttributeView(_View):
    id: str
    category_id: str
    code: str
    name: str
    value_type: AttributeValueType
    unit: str | None
    is_comparable: bool
    is_filterable: bool


# ---------- 商品 ----------


class ProductInput(BaseModel):
    category_id: str
    product_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    brand: str | None = Field(default=None, max_length=128)
    description: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductView(_View):
    id: str
    category_id: str
    product_code: str
    name: str
    brand: str | None
    description: str | None
    attributes: dict[str, Any] | None
    status: ProductStatus
    version: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ---------- 购买规格 ----------


class OptionInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0


class OptionView(_View):
    id: str
    product_id: str
    code: str
    name: str
    sort_order: int


class OptionValueInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    display_value: str = Field(min_length=1, max_length=255)
    sort_order: int = 0


class OptionValueView(_View):
    id: str
    option_id: str
    code: str
    display_value: str
    sort_order: int


# ---------- SKU ----------


class SKUInput(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64)
    # 无选项商品留空；有选项商品必须为每个选项各给出一个值。
    option_value_ids: list[str] = Field(default_factory=list)


class SKUView(_View):
    id: str
    product_id: str
    sku_code: str
    option_signature: str
    status: SKUStatus
    version: int


# ---------- 媒体 ----------


class MediaInput(BaseModel):
    url: str = Field(min_length=1, max_length=512)
    media_type: str = Field(min_length=1, max_length=32)
    sku_id: str | None = None
    alt_text: str | None = Field(default=None, max_length=255)
    sort_order: int = 0


class MediaView(_View):
    id: str
    product_id: str
    sku_id: str | None
    url: str
    media_type: str
    alt_text: str | None
    sort_order: int


# ---------- 读取视图 ----------


class OptionDetailView(OptionView):
    """购买规格及其可选值。"""

    values: list[OptionValueView] = Field(default_factory=list)


class SelectedOptionValueView(BaseModel):
    """SKU 选中的规格值，带规格与值的展示信息。"""

    option_id: str
    option_code: str
    option_name: str
    value_id: str
    value_code: str
    display_value: str


class SKUDetailView(SKUView):
    """SKU 及其确定的选项组合。

    `status` 与所属商品的 `product_status` 都显式返回：SKU 可售的前提是
    商品已上架（`active`）且 SKU 未停用，缺任一项都不可购买。
    """

    product_status: ProductStatus
    selected_values: list[SelectedOptionValueView] = Field(default_factory=list)


class CategoryRefView(_View):
    id: str
    code: str
    name: str


class ProductDetailView(ProductView):
    """商品详情：真实 SKU 组合、购买规格、媒体与类目属性定义。"""

    category: CategoryRefView
    category_attributes: list[AttributeView] = Field(default_factory=list)
    options: list[OptionDetailView] = Field(default_factory=list)
    skus: list[SKUDetailView] = Field(default_factory=list)
    media: list[MediaView] = Field(default_factory=list)


class ProductSummaryView(_View):
    """列表页使用的商品摘要。"""

    id: str
    category_id: str
    product_code: str
    name: str
    brand: str | None
    status: ProductStatus
    version: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ProductPage(BaseModel):
    """按稳定游标翻页的商品列表。"""

    items: list[ProductSummaryView] = Field(default_factory=list)
    next_cursor: str | None = None


class SaleFactView(BaseModel):
    """同事务锁定的可售商品与 SKU；不包含价格、库存。"""
    product: ProductView
    sku: SKUDetailView
    category_attributes: list[AttributeView] = Field(default_factory=list)
