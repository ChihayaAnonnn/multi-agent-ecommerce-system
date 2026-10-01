"""公共只读商品展厅 API，交易仍使用已验证主体的购物接口。"""
from fastapi import APIRouter,Depends,Query,HTTPException
from sqlalchemy.exc import SQLAlchemyError
from api.runtime import require_catalog,require_pricing,require_inventory
from api.catalog import catalog_http_error
from catalog.service import CatalogError,CatalogService
from pricing.service import PricingService
from inventory.service import InventoryService
from commerce_orchestrator.facts import CommerceFactsReader,CommerceDependencyError
from storefront.service import StorefrontService
from storefront.schemas import StorefrontPage,StorefrontResolved,ResolveRequest
router=APIRouter(prefix='/api/v1/storefront',tags=['storefront'])

def service(catalog: CatalogService=Depends(require_catalog),pricing: PricingService=Depends(require_pricing),inventory: InventoryService=Depends(require_inventory)) -> StorefrontService:
    return StorefrontService(catalog,CommerceFactsReader(catalog,pricing,inventory))

async def read(awaitable):
    try:return await awaitable
    except CatalogError as exc:raise catalog_http_error(exc) from exc
    except (SQLAlchemyError,CommerceDependencyError) as exc:
        raise HTTPException(503,detail={'code':'dependency_unavailable'}) from exc

@router.get('/products',response_model=StorefrontPage)
async def products(limit: int=Query(24,ge=1,le=100),cursor: str | None=None,reader: StorefrontService=Depends(service)):
    return await read(reader.list_products(limit=limit,cursor=cursor))

@router.post('/products/resolve',response_model=StorefrontResolved)
async def resolve(body: ResolveRequest,reader: StorefrontService=Depends(service)):
    return await read(reader.resolve_products(body.product_ids))
