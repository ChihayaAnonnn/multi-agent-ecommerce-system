"""目录领域输入输出 DTO。

输入 DTO 只做形状与长度校验；业务规则（编码唯一、选项组合完整性、属性类型、
上架条件）由 catalog.service 在事务内校验。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime
    updated_at: datetime


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
