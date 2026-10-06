"""已验证主体的购物入口与统一领域错误映射。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from api.contracts.commerce import (
    CheckoutHandoffRequest,
    CheckoutPreviewRequest,
    CompareRequest,
    ConfirmationRequest,
    CreateSessionRequest,
    EventRequest,
    ProductContextRequest,
)
from api.deps import require_catalog, require_inventory, require_pricing
from api.errors import catalog_http_error, commerce_http_error
from api.routes.auth import require_principal
from application.shopping.actions import CartActionService
from application.shopping.contracts import TurnRequest, TurnResponse
from kernel.errors import CommerceDependencyError, CommerceError
from modules.catalog.service import CatalogError, CatalogService
from modules.inventory.service import InventoryService
from modules.pricing.service import PricingService
from modules.sale_facts.service import SaleFactsReader
from modules.sessions.schemas import ShoppingSessionView
from modules.sessions.service import ShoppingSessionService
from shared.errors import DomainError
from shared.principal import Principal

router = APIRouter(prefix='/api/v1/commerce', tags=['commerce'])


def sessions(request: Request) -> ShoppingSessionService:
    service = getattr(request.app.state.runtime, 'shopping_sessions', None)
    if service is None:
        raise HTTPException(503, detail={'code':'dependency_unavailable'})
    return service


async def invoke(awaitable):
    try:
        return await awaitable
    except DomainError as exc:
        raise commerce_http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise commerce_http_error(CommerceDependencyError()) from exc


@router.post('/sessions', response_model=ShoppingSessionView)
async def create_session(principal: Principal = Depends(require_principal), service: ShoppingSessionService = Depends(sessions), body: CreateSessionRequest | None = None):
    return await invoke(service.create(principal))


@router.get('/sessions/{session_id}', response_model=ShoppingSessionView)
async def get_session(session_id: str, principal: Principal = Depends(require_principal), service: ShoppingSessionService = Depends(sessions)):
    return await invoke(service.get_owned(session_id, principal))


def orchestrator(request: Request):
    service = getattr(request.app.state.runtime, 'commerce', None)
    if service is None:
        raise HTTPException(503, detail={'code':'dependency_unavailable'})
    return service


@router.post('/sessions/{session_id}/turns', response_model=TurnResponse)
async def turn(session_id: str, body: TurnRequest, principal: Principal = Depends(require_principal), service=Depends(orchestrator)):
    return await invoke(service.turn(principal,session_id,body))


@router.post('/sessions/{session_id}/compare')
async def compare(session_id: str, body: CompareRequest, principal: Principal = Depends(require_principal), service=Depends(orchestrator)):
    return await invoke(service.compare(principal,session_id,body.product_ids))


def carts(request: Request):
    service=getattr(request.app.state.runtime,'cart',None)
    if service is None:
        raise HTTPException(503,detail={'code':'dependency_unavailable'})
    return service


@router.get('/sessions/{session_id}/cart')
async def get_cart(session_id: str, principal: Principal=Depends(require_principal), session_service=Depends(sessions), cart_service=Depends(carts)):
    await invoke(session_service.get_owned(session_id,principal))
    return await invoke(cart_service.get_cart(principal))


def actions(request: Request) -> CartActionService:
    service = getattr(request.app.state.runtime, 'cart_actions', None)
    if service is None:
        raise HTTPException(503, detail={'code':'dependency_unavailable'})
    return service


@router.post('/sessions/{session_id}/actions/{action_id}/confirm')
async def confirm_action(
    session_id: str,
    action_id: str,
    body: ConfirmationRequest,
    principal: Principal = Depends(require_principal),
    service=Depends(actions),
):
    return await invoke(service.confirm(principal,session_id,action_id,body.approved,body.idempotency_key))


def checkout(request: Request):
    service=getattr(request.app.state.runtime,'cart_actions',None)
    if service is None or service.checkout is None:
        raise HTTPException(503,detail={'code':'checkout_unavailable'})
    return service


@router.post('/sessions/{session_id}/checkout-preview')
async def checkout_preview(session_id: str,body: CheckoutPreviewRequest,principal: Principal=Depends(require_principal),service=Depends(checkout)):
    return await invoke(
        service.preview_checkout(principal,session_id,body.cart_version)
    )


@router.post('/sessions/{session_id}/checkout-handoff')
async def checkout_handoff(session_id: str,body: CheckoutHandoffRequest,principal: Principal=Depends(require_principal),service=Depends(checkout)):
    return await invoke(service.confirm_handoff(principal,session_id,body.action_id,body.approved,body.idempotency_key))


@router.get('/sessions/{session_id}/handoffs/{handoff_id}')
async def get_handoff(session_id: str,handoff_id: str,principal: Principal=Depends(require_principal),service=Depends(checkout)):
    return await invoke(service.get_handoff(principal,session_id,handoff_id))


def events(request: Request):
    service=getattr(request.app.state.runtime,'commerce_events',None)
    if service is None:
        raise HTTPException(503,detail={'code':'event_unavailable'})
    return service


@router.post('/events')
async def record_event(body: EventRequest,response: Response,principal: Principal=Depends(require_principal),service=Depends(events)):
    receipt=await invoke(service.record(principal,str(body.session_id),str(body.event_id),body.request_id,body.kind,body.payload.model_dump(mode='json',exclude_none=True)))
    if not receipt.accepted:
        response.status_code=503
    return receipt

# 展厅显式商品引用：返回服务端事实后记录展示来源，绝不接受自报价格或可售状态。


@router.post('/sessions/{session_id}/product-context')
async def product_context(session_id: str, body: ProductContextRequest,
    principal: Principal=Depends(require_principal), session_service: ShoppingSessionService=Depends(sessions),
    catalog: CatalogService=Depends(require_catalog), pricing: PricingService=Depends(require_pricing),
    inventory: InventoryService=Depends(require_inventory)):
    async def resolve_context():
        await session_service.get_owned(session_id,principal)
        try:
            product=await catalog.get_product(body.product_id)
        except CatalogError as exc:
            raise catalog_http_error(exc) from exc
        sku=next((s for s in product.skus if s.id==body.sku_id and s.status=='active'),None)
        if product.status!='active' or sku is None:
            raise CommerceError('invalid_product_reference')
        facts = SaleFactsReader(catalog, pricing, inventory)
        quote = (await facts.quote_many([sku.id]))[sku.id]
        await session_service.record_seen(session_id,principal,[product.id],[sku.id])
        return {'product_id':product.id,'sku_id':sku.id,'quote':quote}
    return await invoke(resolve_context())
