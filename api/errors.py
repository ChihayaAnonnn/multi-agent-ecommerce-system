"""领域错误的兼容 HTTP 映射；未登记错误沿用内部 500 策略。"""

from fastapi import HTTPException

from catalog.service import CatalogConflictError, CatalogError, CatalogNotFoundError
from kernel.errors import CommerceDependencyError, CommerceError
from shared.errors import DomainError

COMMERCE_HTTP_STATUSES = {
    'not_found': 404,
    'session_expired': 409,
    'idempotency_conflict': 409,
    'turn_processing': 409,
    'turn_lease_lost': 409,
    'cart_version_conflict': 409,
    'action_already_resolved': 409,
    'action_expired': 409,
    'invalid_completion': 422,
    'invalid_action': 422,
    'invalid_quantity': 422,
    'constraints_mismatch': 422,
    'insufficient_stock': 422,
    'invalid_confirmation': 422,
    'invalid_comparison': 422,
    'not_seen': 422,
    'comparison_category_mismatch': 422,
    'invalid_product_reference': 422,
    'invalid_event_input': 422,
    'sku_not_found': 422,
    'sku_inactive': 422,
    'product_not_active': 422,
    'price_missing': 422,
    'stock_unknown': 422,
    'out_of_stock': 422,
    'checkout_unavailable': 503,
}


def commerce_http_error(exc: DomainError) -> HTTPException:
    """保留购物错误字典；未知类型或错误码不得被误判为输入错误。"""
    if isinstance(exc, CommerceDependencyError):
        return HTTPException(503, detail={'code': 'dependency_unavailable'})
    if isinstance(exc, CommerceError) and exc.code in COMMERCE_HTTP_STATUSES:
        return HTTPException(
            COMMERCE_HTTP_STATUSES[exc.code], detail={'code': exc.code, **exc.details},
        )
    raise exc


def catalog_http_error(exc: CatalogError) -> HTTPException:
    """目录端点继续返回字符串，而非购物错误字典。"""
    if isinstance(exc, CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CatalogConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))
