"""公共只读商品展厅 API，交易仍使用已验证主体的购物接口。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import SQLAlchemyError

from ecom.api.deps import require_catalog, require_inventory, require_pricing
from ecom.api.errors import catalog_http_error, commerce_http_error
from ecom.application.storefront.schemas import (
    ResolveRequest,
    StorefrontPage,
    StorefrontResolved,
)
from ecom.application.storefront.service import StorefrontService
from ecom.kernel.errors import CommerceDependencyError
from ecom.modules.catalog.service import CatalogError, CatalogService
from ecom.modules.inventory.service import InventoryService
from ecom.modules.pricing.service import PricingService
from ecom.modules.sale_facts.service import SaleFactsReader

router=APIRouter(prefix='/api/v1/storefront',tags=['storefront'])

def service(catalog: CatalogService=Depends(require_catalog),pricing: PricingService=Depends(require_pricing),inventory: InventoryService=Depends(require_inventory)) -> StorefrontService:
    return StorefrontService(catalog,SaleFactsReader(catalog,pricing,inventory))

async def read(awaitable):
    try:return await awaitable
    except CatalogError as exc:raise catalog_http_error(exc) from exc
    except (SQLAlchemyError,CommerceDependencyError) as exc:
        raise commerce_http_error(CommerceDependencyError()) from exc

@router.get('/products',response_model=StorefrontPage)
async def products(limit: int=Query(24,ge=1,le=100),cursor: str | None=None,reader: StorefrontService=Depends(service)):
    return await read(reader.list_products(limit=limit,cursor=cursor))

@router.post('/products/resolve',response_model=StorefrontResolved)
async def resolve(body: ResolveRequest,reader: StorefrontService=Depends(service)):
    return await read(reader.resolve_products(body.product_ids))
