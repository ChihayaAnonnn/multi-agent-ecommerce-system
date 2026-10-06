"""应用层结账服务的契约边界（无外部依赖）。"""

import inspect

from ecom.modules.checkout.service import CheckoutService


def test_checkout_no_longer_coordinates_session_or_turn():
    parameters = inspect.signature(CheckoutService).parameters
    assert 'sessions' not in parameters
    assert not any(
        hasattr(CheckoutService, name)
        for name in ('preview', 'confirm_handoff', 'get_owned')
    )
