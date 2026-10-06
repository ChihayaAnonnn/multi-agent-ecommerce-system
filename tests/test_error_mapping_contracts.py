"""领域错误与既有 HTTP 响应边界的兼容合同。"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from api import identity
from api.commerce import invoke
from catalog.service import (
    CatalogConflictError,
    CatalogError,
    CatalogNotFoundError,
    CatalogValidationError,
)
from identity.passwords import PasswordPolicyError
from identity.types import IdentityError
from inventory.service import InventoryError
from kernel.errors import CommerceDependencyError, CommerceError
from pricing.service import PricingError
from shared.errors import DomainError
from shopping_session.service import CommerceError as SessionCommerceError

COMMERCE_STATUSES = {
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


def test_commerce_error_does_not_carry_http_status():
    error = CommerceError('cart_version_conflict', {'version': 2})
    assert not hasattr(error, 'status_code')
    from api.errors import commerce_http_error

    response = commerce_http_error(error)
    assert response.status_code == 409
    assert response.detail == {'code': 'cart_version_conflict', 'version': 2}
    assert SessionCommerceError is CommerceError


@pytest.mark.parametrize('code,status', COMMERCE_STATUSES.items())
def test_registered_commerce_errors_preserve_status_and_detail(code, status):
    error = CommerceError(code)
    assert not hasattr(error, 'status_code')
    from api.errors import commerce_http_error

    response = commerce_http_error(error)
    assert response.status_code == status
    assert response.detail == {'code': code}


def test_turn_processing_keeps_retryable_detail():
    error = CommerceError('turn_processing', {'retryable': True})
    assert not hasattr(error, 'status_code')
    from api.errors import commerce_http_error

    response = commerce_http_error(error)
    assert response.status_code == 409
    assert response.detail == {'code': 'turn_processing', 'retryable': True}


def test_registered_codes_cover_raise_sites_and_dynamic_quote_reasons():
    from api.errors import COMMERCE_HTTP_STATUSES

    root = Path(__file__).resolve().parents[1]
    codes = set()
    dynamic_codes = set()
    for directory in (
        'cart', 'checkout', 'shopping_session', 'commerce_events',
        'commerce_orchestrator', 'api',
    ):
        for path in (root / directory).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not (
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'CommerceError'
                ):
                    continue
                code = node.args[0]
                if isinstance(code, ast.Constant):
                    codes.add(code.value)
                else:
                    dynamic_codes.add(ast.unparse(code))
                assert len(node.args) <= 2
                assert len(node.args) == 1 or not isinstance(node.args[1], ast.Constant)
    assert dynamic_codes == {'quote.reason'}
    quotes = ast.parse((root / 'kernel/quotes.py').read_text(encoding='utf-8'))
    reasons = {
        node.value.value for node in ast.walk(quotes)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str) and node.value.value != 'purchasable'
    }
    assert codes | reasons == set(COMMERCE_HTTP_STATUSES) == set(COMMERCE_STATUSES)


@pytest.mark.parametrize('code,status', COMMERCE_STATUSES.items())
def test_registered_errors_preserve_actual_http_response(code, status):
    async def scenario():
        app = FastAPI()

        @app.get('/error')
        async def fail():
            async def operation():
                raise CommerceError(code)

            return await invoke(operation())

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='https://test',
        ) as client:
            response = await client.get('/error')
        assert response.status_code == status
        assert response.json() == {'detail': {'code': code}}

    asyncio.run(scenario())


@pytest.mark.parametrize('error_type', [
    CommerceError, CatalogError, IdentityError, PricingError, InventoryError,
    PasswordPolicyError,
])
def test_business_errors_share_fieldless_domain_base(error_type):
    assert issubclass(error_type, DomainError)
    error = DomainError('内部错误')
    assert not hasattr(error, 'code')
    assert not hasattr(error, 'status_code')


def test_password_policy_error_preserves_value_error_compatibility():
    error = PasswordPolicyError('密码不符合策略')
    assert isinstance(error, ValueError)
    assert isinstance(error, DomainError)


def test_password_policy_public_export_is_same_object():
    from identity import service

    assert getattr(service, 'PasswordPolicyError', None) is PasswordPolicyError
    assert 'PasswordPolicyError' in service.__all__


def test_auth_imports_password_policy_from_public_service():
    from api import auth
    from identity import service

    source = Path(auth.__file__).read_text(encoding='utf-8')
    imports = {
        (node.module, alias.name)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ('identity.passwords', 'PasswordPolicyError') not in imports
    assert ('identity.service', 'PasswordPolicyError') in imports
    assert auth.PasswordPolicyError is service.PasswordPolicyError


@pytest.mark.parametrize('error_type,status', [
    (CatalogNotFoundError, 404), (CatalogConflictError, 409),
    (CatalogValidationError, 422), (CatalogError, 422),
])
def test_catalog_errors_keep_string_details(error_type, status):
    assert issubclass(error_type, DomainError)
    from api.errors import catalog_http_error

    response = catalog_http_error(error_type('目录业务错误'))
    assert response.status_code == status
    assert response.detail == '目录业务错误'


def test_identity_http_configuration_belongs_to_api():
    assert identity.IdentityHttpConfig.__module__ == 'api.http_config'
    assert identity.parse_trusted_networks.__module__ == 'api.http_config'
    config = identity.IdentityHttpConfig()
    assert config.refresh_cookie_name == '__Host-ecom_refresh'
    assert config.session_ttl_seconds == 2592000
    assert config.allowed_origins == config.trusted_proxy_networks == ()


def test_http_list_parsing_is_not_settings_business_behavior():
    from config.settings import Settings

    assert not hasattr(Settings, 'allowed_origins')
    assert not hasattr(Settings, 'trusted_proxy_networks')
    from api.http_config import parse_http_list

    assert parse_http_list(' https://one.test, ,https://two.test ') == (
        'https://one.test', 'https://two.test',
    )


def test_trusted_network_validation_remains_fail_closed():
    assert identity.parse_trusted_networks.__module__ == 'api.http_config'
    from identity.types import IdentityConfigurationError

    parsed = identity.parse_trusted_networks(('10.0.0.1/8', '2001:db8::/32'))
    assert tuple(str(network) for network in parsed) == (
        '10.0.0.0/8', '2001:db8::/32',
    )
    with pytest.raises(IdentityConfigurationError, match='ECOM_IDENTITY'):
        identity.parse_trusted_networks(('invalid-cidr',))


@pytest.mark.parametrize('error', [
    SQLAlchemyError('SELECT secret FROM private_data'),
    CommerceDependencyError('private dependency details'),
])
def test_dependency_errors_keep_safe_commerce_details(error):
    async def fail():
        raise error

    async def scenario():
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            await invoke(fail())
        assert caught.value.status_code == 503
        assert caught.value.detail == {'code': 'dependency_unavailable'}

    asyncio.run(scenario())


@pytest.mark.parametrize('error', [
    DomainError('SELECT secret FROM private_data'),
    CommerceError('unregistered_internal_error', {'sql': 'SELECT secret'}),
])
def test_unknown_domain_error_keeps_internal_error_policy(error):
    async def scenario():
        app = FastAPI()

        @app.get('/error')
        async def fail():
            async def operation():
                raise error

            return await invoke(operation())

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url='https://test',
        ) as client:
            response = await client.get('/error')
        assert response.status_code == 500
        assert response.text == 'Internal Server Error'

    asyncio.run(scenario())


def test_identity_error_response_side_effects_are_preserved():
    from api.auth import router
    from identity.types import RateLimited

    class IdentityStub:
        async def login(self, *args):
            raise RateLimited(17)

        async def logout(self, *args):
            return None

    async def scenario():
        app = FastAPI()
        app.include_router(router)
        app.state.runtime = SimpleNamespace(
            identity=IdentityStub(), identity_config=identity.IdentityHttpConfig(),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='https://test',
        ) as client:
            limited = await client.post('/api/v1/auth/login', json={
                'email': 'synthetic@example.test', 'password': 'synthetic-password',
            })
            assert limited.status_code == 429
            assert limited.headers['Retry-After'] == '17'
            assert limited.headers['Cache-Control'] == 'no-store'
            response = await client.post('/api/v1/auth/logout', headers={
                'Cookie': '__Host-ecom_refresh=synthetic-refresh',
                'X-CSRF-Token': 'synthetic-csrf',
            })
            assert response.status_code == 204
            assert response.headers['Cache-Control'] == 'no-store'
            cookie = response.headers['Set-Cookie']
            assert '__Host-ecom_refresh=' in cookie and 'Max-Age=0' in cookie
            assert 'HttpOnly' in cookie and 'Secure' in cookie
            assert 'Path=/' in cookie and 'SameSite=lax' in cookie

    asyncio.run(scenario())
