"""展示 DTO，不作为交易授权或当前价格承诺。"""
from pydantic import BaseModel,Field,ConfigDict
from catalog.schemas import CategoryRefView
from pricing.schemas import PriceView
from inventory.schemas import AvailabilityView

class StorefrontProduct(BaseModel):
    id: str
    name: str
    brand: str | None
    category: CategoryRefView
    image_url: str | None
    sku_id: str | None
    price: PriceView | None
    availability: AvailabilityView | None
    purchasable: bool
    reason: str
    simulated: bool

class StorefrontPage(BaseModel):
    items: list[StorefrontProduct]
    next_cursor: str | None

class StorefrontResolved(BaseModel):
    items: list[StorefrontProduct]
    missing_product_ids: list[str]

class ResolveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_ids: list[str]=Field(max_length=100)


__all__ = [
    'ResolveRequest',
    'StorefrontPage',
    'StorefrontProduct',
    'StorefrontResolved',
]
