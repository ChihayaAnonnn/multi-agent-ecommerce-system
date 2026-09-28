"""消费者只读的目录端点：商品列表、商品详情与单个 SKU。

这些端点只拼装领域 DTO，不直接访问 ORM，也不暴露受控写入命令。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.runtime import require_catalog
from catalog.schemas import ProductDetailView, ProductPage, SKUDetailView
from catalog.service import (
    CatalogConflictError,
    CatalogError,
    CatalogNotFoundError,
    CatalogService,
)

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


def catalog_http_error(exc: CatalogError) -> HTTPException:
    """把领域异常映射为 HTTP 状态码。"""
    if isinstance(exc, CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CatalogConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


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
