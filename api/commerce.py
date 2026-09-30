"""已验证主体的购物入口与统一领域错误映射。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from api.identity import require_principal
from identity.types import Principal
from models.commerce import CreateSessionRequest
from shopping_session.schemas import ShoppingSessionView
from shopping_session.service import ShoppingSessionService, CommerceError

router = APIRouter(prefix='/api/v1/commerce', tags=['commerce'])


def sessions(request: Request) -> ShoppingSessionService:
    service = getattr(request.app.state.runtime, 'shopping_sessions', None)
    if service is None:
        raise HTTPException(503, detail={'code':'dependency_unavailable'})
    return service


async def invoke(awaitable):
    try:
        return await awaitable
    except CommerceError as exc:
        raise HTTPException(exc.status_code, detail={'code':exc.code, **exc.details}) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail={'code':'dependency_unavailable'}) from exc


@router.post('/sessions', response_model=ShoppingSessionView)
async def create_session(principal: Principal = Depends(require_principal), service: ShoppingSessionService = Depends(sessions), body: CreateSessionRequest | None = None):
    return await invoke(service.create(principal))


@router.get('/sessions/{session_id}', response_model=ShoppingSessionView)
async def get_session(session_id: str, principal: Principal = Depends(require_principal), service: ShoppingSessionService = Depends(sessions)):
    return await invoke(service.get_owned(session_id, principal))
