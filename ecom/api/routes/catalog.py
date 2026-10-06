"""消费者只读的目录与报价端点：商品列表、商品详情、单个 SKU 与 SKU 报价。

目录端点只拼装领域 DTO，不直接访问 ORM，也不暴露受控写入命令。
报价只做只读拼装：SKU、现行价格与可售状态分别来自目录、价格、库存三个领域模块。
缺价、库存未知或查询故障都不得被当作可购买。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ecom.api.deps import require_catalog, require_inventory, require_pricing
from ecom.api.errors import catalog_http_error as catalog_http_error
from ecom.kernel.quotes import evaluate_legacy_api_purchasability
from ecom.modules.catalog.schemas import ProductDetailView, ProductPage, SKUDetailView
from ecom.modules.catalog.service import (
    CatalogError,
    CatalogService,
)
from ecom.modules.inventory.schemas import AvailabilityView
from ecom.modules.inventory.service import InventoryDependencyError, InventoryService
from ecom.modules.pricing.schemas import PriceView
from ecom.modules.pricing.service import PricingDependencyError, PricingService
from ecom.shared.time import UtcDateTime, utcnow

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get("/products", response_model=ProductPage)
async def list_products(
    category_id: str | None = None,
    text: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    catalog: CatalogService = Depends(require_catalog),
) -> ProductPage:
    """在售商品列表；只返回 active 商品，按稳定游标翻页。"""
    try:
        return await catalog.search(
            category_id=category_id, text=text, limit=limit, cursor=cursor
        )
    except CatalogError as exc:
        raise catalog_http_error(exc) from exc


@router.get("/products/{product_id}", response_model=ProductDetailView)
async def get_product(
    product_id: str,
    catalog: CatalogService = Depends(require_catalog),
) -> ProductDetailView:
    """商品详情，含 SKU、购买规格与媒体；已归档商品由 status 区分。"""
    try:
        return await catalog.get_product(product_id)
    except CatalogError as exc:
        raise catalog_http_error(exc) from exc


@router.get("/skus/{sku_id}", response_model=SKUDetailView)
async def get_sku(
    sku_id: str,
    catalog: CatalogService = Depends(require_catalog),
) -> SKUDetailView:
    """单个 SKU 及其确定的选项组合；停用状态显式返回。"""
    try:
        return await catalog.get_sku(sku_id)
    except CatalogError as exc:
        raise catalog_http_error(exc) from exc


# 报价端点原属 api/routes/commerce_read.py，合并后保留独立 router 与 /api/v1/commerce
# 前缀，使 main.py 的 include 顺序与既有路径合同逐字不变。
quote_router = APIRouter(prefix="/api/v1/commerce", tags=["commerce"])

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
    read_at: UtcDateTime


def _quote_reason(
    sku: SKUDetailView,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    """保留 API 弱规则；R 批准前不归一非 CNY 与负库存语义。"""
    return evaluate_legacy_api_purchasability(sku, price, availability)


@quote_router.get("/skus/{sku_id}/quote", response_model=QuoteView)
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
