"""消费者的 SKU 报价端点。

报价只做只读拼装：SKU、现行价格与可售状态分别来自目录、价格、库存三个领域模块。
缺价、库存未知或查询故障都不得被当作可购买。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.catalog import catalog_http_error
from api.runtime import require_catalog, require_inventory, require_pricing
from catalog.schemas import SKUDetailView
from catalog.service import CatalogError, CatalogService
from db.base import utcnow
from inventory.schemas import AvailabilityView
from inventory.service import InventoryDependencyError, InventoryService
from pricing.schemas import PriceView
from pricing.service import PricingDependencyError, PricingService

router = APIRouter(prefix="/api/v1/commerce", tags=["commerce"])

# 报价不可购买的原因码；与 HTTP 状态分开，便于调用方判断。
REASON_PURCHASABLE = "purchasable"
REASON_SKU_INACTIVE = "sku_inactive"
REASON_PRODUCT_NOT_ACTIVE = "product_not_active"
REASON_PRICE_MISSING = "price_missing"
REASON_STOCK_UNKNOWN = "stock_unknown"
REASON_OUT_OF_STOCK = "out_of_stock"


class QuoteView(BaseModel):
    """SKU 报价：价格与库存分别带版本，金额始终是整数分。"""

    sku: SKUDetailView
    price: PriceView | None = None
    availability: AvailabilityView | None = None
    purchasable: bool
    reason: str
    read_at: datetime


def _quote_reason(sku: SKUDetailView, price, availability) -> str:
    if sku.status != "active":
        return REASON_SKU_INACTIVE
    # 商品未上架（草稿或已归档）时，SKU 有价有货也不构成可购买
    if sku.product_status != "active":
        return REASON_PRODUCT_NOT_ACTIVE
    if price is None:
        return REASON_PRICE_MISSING
    if availability is None or availability.status == "unknown":
        return REASON_STOCK_UNKNOWN
    if availability.status != "available" or not availability.available_quantity:
        return REASON_OUT_OF_STOCK
    return REASON_PURCHASABLE


@router.get("/skus/{sku_id}/quote", response_model=QuoteView)
async def quote_sku(
    sku_id: str,
    catalog: CatalogService = Depends(require_catalog),
    pricing: PricingService = Depends(require_pricing),
    inventory: InventoryService = Depends(require_inventory),
) -> QuoteView:
    """返回 SKU 的现行价格与可售状态。

    不存在的 SKU 返回 404；库存或价格后端故障返回 503，绝不返回可购买。
    """
    try:
        sku = await catalog.get_sku(sku_id)
    except CatalogError as exc:
        raise catalog_http_error(exc) from exc

    try:
        availability = (await inventory.get_availability([sku_id])).get(sku_id)
    except InventoryDependencyError as exc:
        raise HTTPException(status_code=503, detail="库存服务不可用，无法报价") from exc

    try:
        price = (await pricing.get_prices([sku_id])).get(sku_id)
    except PricingDependencyError as exc:
        raise HTTPException(status_code=503, detail="价格服务不可用，无法报价") from exc

    reason = _quote_reason(sku, price, availability)

    return QuoteView(
        sku=sku,
        price=price,
        availability=availability,
        purchasable=reason == REASON_PURCHASABLE,
        reason=reason,
        read_at=utcnow(),
    )
