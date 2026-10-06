"""结账事务接口的类型契约与网关端口别名（无外部依赖）。"""

import inspect
from typing import get_type_hints

import pytest

from ecom.modules.checkout.service import CheckoutService


@pytest.mark.parametrize('name', [
    'preview_in_transaction', 'prepare_handoff_in_transaction',
    'request_destination', 'finalize_handoff_in_transaction',
    'get_owned_in_transaction',
])
def test_checkout_transaction_public_methods_are_typed(name):
    assert hasattr(CheckoutService, name), f'missing transaction API: {name}'
    method = getattr(CheckoutService, name)
    assert inspect.iscoroutinefunction(method)
    hints = get_type_hints(method)
    assert 'return' in hints
    assert all(parameter in hints for parameter in inspect.signature(method).parameters
               if parameter != 'self')


def test_gateway_port_preserves_alias_identity():
    from ecom.infra import checkout_gateway as gateway
    from ecom.modules.checkout import ports

    assert gateway.HostCheckoutGateway is ports.HostCheckoutGateway
