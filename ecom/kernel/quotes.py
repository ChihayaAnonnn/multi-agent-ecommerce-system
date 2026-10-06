"""可信报价 DTO 与完整可购买性纯规则。"""

from pydantic import BaseModel, Field

from ecom.modules.catalog.schemas import SaleFactView, SKUDetailView
from ecom.modules.inventory.schemas import AvailabilityView
from ecom.modules.pricing.schemas import PriceView
from ecom.shared.time import UtcDateTime

__all__ = [
    'SKUQuote', 'CandidatePage', 'evaluate_purchasability',
    'evaluate_legacy_api_purchasability', 'evaluate_legacy_cart_purchasability',
]


class SKUQuote(BaseModel):
    sku_id: str
    sku: SKUDetailView | None = None
    price: PriceView | None = None
    availability: AvailabilityView | None = None
    purchasable: bool
    reason: str
    read_at: UtcDateTime


class CandidatePage(BaseModel):
    items: list[SKUQuote] = Field(default_factory=list)
    incomplete: bool = False
    scanned: int = 0


def evaluate_purchasability(
    sku: SKUDetailView | None,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    if sku is None:
        return 'sku_not_found'
    if sku.status != 'active':
        return 'sku_inactive'
    if sku.product_status != 'active':
        return 'product_not_active'
    if price is None or price.currency != 'CNY':
        return 'price_missing'
    if availability is None or availability.status == 'unknown':
        return 'stock_unknown'
    if (availability.status != 'available' or not availability.available_quantity
            or availability.available_quantity < 1):
        return 'out_of_stock'
    return 'purchasable'


def evaluate_legacy_api_purchasability(
    sku: SKUDetailView,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    """API 临时纯包装；R 批准前保留非 CNY 与负库存语义。"""
    if sku.status != 'active':
        return 'sku_inactive'
    if sku.product_status != 'active':
        return 'product_not_active'
    if price is None:
        return 'price_missing'
    if availability is None or availability.status == 'unknown':
        return 'stock_unknown'
    if availability.status != 'available' or not availability.available_quantity:
        return 'out_of_stock'
    return 'purchasable'


def evaluate_legacy_cart_purchasability(
    sale: SaleFactView | None,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    """锁车临时纯包装；R 前保留商品权威状态及币种、数量、状态差异。"""
    if sale is None:
        return 'sku_not_found'
    if sale.sku.status != 'active':
        return 'sku_inactive'
    if sale.product.status != 'active':
        return 'product_not_active'
    if price is None:
        return 'price_missing'
    if availability is None or availability.status == 'unknown':
        return 'stock_unknown'
    if not availability.available_quantity:
        return 'out_of_stock'
    return 'purchasable'
