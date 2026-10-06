"""内部事务接口与 HTTP 视图分离的契约。"""

import inspect
from typing import get_type_hints

import pytest

from modules.cart import schemas
from modules.cart.service import CartService
from modules.sessions.service import ShoppingSessionService
from shared.hashing import JsonObject


@pytest.mark.parametrize(
    'name,fields',
    [
        (
            'CartLineSnapshot',
            {
                'line_id',
                'sku_id',
                'quantity',
                'product_name_snapshot',
                'sku_options_snapshot',
                'image_url_snapshot',
                'last_seen_unit_amount_minor',
                'last_seen_price_version',
            },
        ),
        ('CartSnapshot', {'cart_id', 'principal_key', 'version', 'lines'}),
        (
            'PendingActionRecord',
            {
                'action_id',
                'session_id',
                'principal_key',
                'cart_id',
                'kind',
                'proposal_hash',
                'status',
                'source_turn_id',
                'confirmation_key',
                'confirmation_request_hash',
                'expected_cart_version',
                'payload',
                'summary',
                'expires_at',
                'created_at',
                'result',
            },
        ),
        (
            'ActionProposalCommand',
            {
                'kind',
                'target',
                'expected_cart_version',
                'source_turn_id',
            },
        ),
    ],
)
def test_transaction_dto_fields(name, fields):
    assert hasattr(schemas, name), f'missing DTO: {name}'
    assert set(getattr(schemas, name).model_fields) == fields


@pytest.mark.parametrize(
    'service,name',
    [
        (ShoppingSessionService, 'lock_owned_session'),
        (ShoppingSessionService, 'has_seen_sku'),
        (ShoppingSessionService, 'assert_current_turn'),
        *[
            (CartService, name)
            for name in (
                'lock_cart_snapshot',
                'lock_pending_action',
                'find_action_owner',
                'propose_action_in_transaction',
                'confirm_in_transaction',
                'find_source_action_in_transaction',
                'create_pending_action_in_transaction',
                'confirmation_replay_in_transaction',
                'resolve_action_in_transaction',
                'invalidate_action_in_transaction',
            )
        ],
    ],
)
def test_transaction_public_methods_are_typed(service, name):
    assert hasattr(service, name), f'missing transaction API: {name}'
    method = getattr(service, name)
    assert inspect.iscoroutinefunction(method)
    hints = get_type_hints(method)
    assert 'return' in hints
    assert all(
        parameter in hints
        for parameter in inspect.signature(method).parameters
        if parameter != 'self'
    )


def test_pure_quote_snapshot_is_typed_json():
    assert get_type_hints(CartService.quote_snapshot)['sale'] is not None
    assert get_type_hints(CartService.quote_snapshot)['return'] == JsonObject
