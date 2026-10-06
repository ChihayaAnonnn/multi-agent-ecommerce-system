"""共享购物契约、纯规则与临时弱路径的行为合同。"""

import asyncio
import importlib
import json
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from catalog.schemas import AttributeView, ProductView, SaleFactView, SKUDetailView
from inventory.schemas import AvailabilityView
from pricing.schemas import PriceView

READ_AT = datetime(2026, 10, 6, 1, 2, 3)


def sku_view(**changes):
    return SKUDetailView(
        id='sku', product_id='product', sku_code='code',
        option_signature='default', version=1,
        **({'status': 'active', 'product_status': 'active'} | changes),
    )


def price_view(currency='CNY', amount_minor=100):
    return PriceView(
        sku_id='sku', currency=currency, amount_minor=amount_minor,
        version=1, effective_at=READ_AT, read_at=READ_AT,
    )


def stock_view(status='available', quantity=1):
    return AvailabilityView(
        sku_id='sku', status=status, available_quantity=quantity, read_at=READ_AT,
    )


def sale_view(sku=None, status='active'):
    return SaleFactView(
        sku=sku or sku_view(),
        product=ProductView(
            id='product', category_id='category', product_code='code', name='商品',
            brand=None, description=None, attributes={}, status=status,
            version=1, created_at=READ_AT, updated_at=READ_AT,
        ),
    )


def test_turn_contract_is_same_object_and_same_json():
    contracts = importlib.import_module('commerce_orchestrator.contracts')
    legacy = importlib.import_module('models.commerce')
    assert legacy.TurnRequest is contracts.TurnRequest
    assert legacy.TurnResponse is contracts.TurnResponse
    request = contracts.TurnRequest(turn_id='turn')
    assert json.loads(request.model_dump_json()) == {
        'turn_id': 'turn', 'message': '', 'constraints': None,
        'selected_product_id': None, 'selected_sku_id': None, 'product_ids': [],
        'quantity': None, 'expected_cart_version': None,
        'respond_to_action_id': None, 'idempotency_key': None,
    }
    response = contracts.TurnResponse(
        request_id='request', session_id='session', turn_id='turn',
        intent='shopping', status='clarify',
    )
    assert json.loads(response.model_dump_json()) == {
        'request_id': 'request', 'session_id': 'session', 'turn_id': 'turn',
        'intent': 'shopping', 'status': 'clarify',
        'constraints': {
            'category_id': None, 'query_text': None, 'budget_max_minor': None,
            'currency': 'CNY', 'purpose': None, 'hard_conditions': [],
            'soft_preferences': [], 'sources': {},
        },
        'items': [], 'question': None, 'proposed_action': None, 'errors': [],
        'action_result': None, 'incomplete': False, 'cart': None,
        'handoff': None, 'comparison': None,
    }
    with pytest.raises(ValidationError):
        contracts.TurnRequest(turn_id='turn', quantity=True)
    with pytest.raises(ValidationError):
        contracts.TurnResponse(**response.model_dump(), extra_field='forbidden')


def test_graph_hints_resolve_real_contracts():
    contracts = importlib.import_module('commerce_orchestrator.contracts')
    state = importlib.import_module('commerce_orchestrator.state')
    intent = importlib.import_module('kernel.intent')
    from commerce_orchestrator.schemas import CommerceGraphState
    from shopping_session.schemas import TurnClaim

    assert CommerceGraphState is state.CommerceGraphState
    hints = get_type_hints(state.CommerceGraphState)
    assert hints['turn'] is contracts.TurnRequest
    assert hints['response'] is contracts.TurnResponse
    assert hints['current'] is intent.ShoppingIntent
    assert hints['claim'] is TurnClaim


@pytest.mark.parametrize('sku,price,stock,expected', [
    (None, None, None, 'sku_not_found'),
    (sku_view(status='inactive'), None, None, 'sku_inactive'),
    (sku_view(product_status='draft'), None, None, 'product_not_active'),
    (sku_view(), None, None, 'price_missing'),
    (sku_view(), price_view('USD'), stock_view(), 'price_missing'),
    (sku_view(), price_view(), None, 'stock_unknown'),
    (sku_view(), price_view(), stock_view('unknown', -1), 'stock_unknown'),
    (sku_view(), price_view(), stock_view('out_of_stock', 1), 'out_of_stock'),
    (sku_view(), price_view(), stock_view(quantity=None), 'out_of_stock'),
    (sku_view(), price_view(), stock_view(quantity=-1), 'out_of_stock'),
    (sku_view(), price_view(), stock_view(quantity=0), 'out_of_stock'),
    (sku_view(), price_view(), stock_view(), 'purchasable'),
    (sku_view(), price_view(amount_minor=0), stock_view(), 'purchasable'),
])
def test_reason_priority_includes_negative_stock(sku, price, stock, expected):
    quotes = importlib.import_module('kernel.quotes')
    assert quotes.evaluate_purchasability(sku, price, stock) == expected


@pytest.mark.parametrize('currency,status,quantity,main,api,cart', [
    ('USD', 'available', 1, 'price_missing', 'purchasable', 'purchasable'),
    ('CNY', 'available', -1, 'out_of_stock', 'purchasable', 'purchasable'),
    ('CNY', 'out_of_stock', 1, 'out_of_stock', 'out_of_stock', 'purchasable'),
    ('CNY', 'unknown', 1, 'stock_unknown', 'stock_unknown', 'stock_unknown'),
    ('CNY', 'available', 0, 'out_of_stock', 'out_of_stock', 'out_of_stock'),
])
def test_legacy_quote_paths_keep_existing_differences(
    currency, status, quantity, main, api, cart,
):
    quotes = importlib.import_module('kernel.quotes')
    from api.commerce_read import _quote_reason
    from cart.service import CartService

    sku, price, stock = sku_view(), price_view(currency), stock_view(status, quantity)
    assert quotes.evaluate_purchasability(sku, price, stock) == main
    assert quotes.evaluate_legacy_api_purchasability(sku, price, stock) == api
    assert quotes.evaluate_legacy_cart_purchasability(
        sale_view(sku), price, stock,
    ) == cart
    assert _quote_reason(sku, price, stock) == api

    class Catalog:
        async def lock_sale_facts(self, database, ids):
            return {'sku': SimpleNamespace(
                sku=sku, product=SimpleNamespace(status='active'),
            )}

    class Pricing:
        async def lock_prices(self, database, ids):
            return {'sku': price}

    class Inventory:
        async def lock_availability(self, database, ids):
            return {'sku': stock}

    from sale_facts.service import SaleFactsReader

    facts = SaleFactsReader(Catalog(), Pricing(), Inventory())
    result, _ = asyncio.run(CartService(None, facts).lock_quotes(None, ['sku']))
    assert result['sku'].reason == cart
    assert result['sku'].purchasable is (cart == 'purchasable')


def test_legacy_rule_public_imports_are_callable():
    exports = {}
    exec('from kernel.quotes import *', exports)
    assert exports['evaluate_legacy_api_purchasability'](
        sku_view(), price_view('USD'), stock_view(quantity=-1),
    ) == 'purchasable'
    assert exports['evaluate_legacy_cart_purchasability'](
        sale_view(), price_view(), stock_view('out_of_stock', 1),
    ) == 'purchasable'


@pytest.mark.parametrize('sku,price,stock,expected', [
    (sku_view(status='inactive'), None, None, 'sku_inactive'),
    (sku_view(product_status='draft'), None, None, 'product_not_active'),
    (sku_view(), None, None, 'price_missing'),
    (sku_view(), price_view(), None, 'stock_unknown'),
    (sku_view(), price_view(), stock_view('unknown', 1), 'stock_unknown'),
    (sku_view(), price_view(), stock_view(quantity=None), 'out_of_stock'),
    (sku_view(), price_view(amount_minor=0), stock_view(), 'purchasable'),
])
def test_public_legacy_api_rule_preserves_reason_priority(sku, price, stock, expected):
    quotes = importlib.import_module('kernel.quotes')
    assert quotes.evaluate_legacy_api_purchasability(sku, price, stock) == expected


@pytest.mark.parametrize('sale,price,stock,expected', [
    (None, None, None, 'sku_not_found'),
    (sale_view(sku_view(status='inactive')), None, None, 'sku_inactive'),
    (sale_view(status='draft'), None, None, 'product_not_active'),
    (sale_view(), None, None, 'price_missing'),
    (sale_view(), price_view(), None, 'stock_unknown'),
    (sale_view(), price_view(), stock_view('unknown', 1), 'stock_unknown'),
    (sale_view(), price_view(), stock_view(quantity=None), 'out_of_stock'),
    (sale_view(), price_view(amount_minor=0), stock_view(), 'purchasable'),
])
def test_public_legacy_cart_rule_preserves_reason_priority(
    sale, price, stock, expected,
):
    quotes = importlib.import_module('kernel.quotes')
    assert quotes.evaluate_legacy_cart_purchasability(sale, price, stock) == expected


@pytest.mark.parametrize('sku_status,product_status,api,cart', [
    ('draft', 'active', 'product_not_active', 'purchasable'),
    ('active', 'draft', 'purchasable', 'product_not_active'),
])
def test_legacy_cart_uses_sale_product_status_when_sources_conflict(
    sku_status, product_status, api, cart,
):
    quotes = importlib.import_module('kernel.quotes')
    from api.commerce_read import _quote_reason
    from cart.service import _legacy_quote_reason

    sku = sku_view(product_status=sku_status)
    sale, price, stock = sale_view(sku, product_status), price_view(), stock_view()
    assert quotes.evaluate_legacy_api_purchasability(sku, price, stock) == api
    assert quotes.evaluate_legacy_cart_purchasability(sale, price, stock) == cart
    assert _quote_reason(sku, price, stock) == api
    assert _legacy_quote_reason(sale, price, stock) == cart


def test_api_legacy_helper_delegates_to_shared_pure_evaluator(monkeypatch):
    from api import commerce_read
    from kernel.quotes import evaluate_purchasability

    monkeypatch.setattr(
        commerce_read, 'evaluate_legacy_api_purchasability',
        evaluate_purchasability, raising=False,
    )
    assert commerce_read._quote_reason(
        sku_view(), price_view('USD'), stock_view(),
    ) == 'price_missing'


def test_cart_legacy_helper_delegates_to_shared_pure_evaluator(monkeypatch):
    from cart import service
    from kernel.quotes import evaluate_purchasability

    def evaluate_sale(sale, price, stock):
        return evaluate_purchasability(sale.sku, price, stock)

    monkeypatch.setattr(
        service, 'evaluate_legacy_cart_purchasability', evaluate_sale, raising=False,
    )
    assert service._legacy_quote_reason(
        sale_view(), price_view('USD'), stock_view(),
    ) == 'price_missing'


@pytest.mark.parametrize('attribute_value,condition_value,source,filterable,expected', [
    (True, True, 'explicit', True, True),
    (True, 1, 'explicit', True, False),
    (1, True, 'explicit', True, False),
    (1, 1.0, 'explicit', True, False),
    ('red', 'red', 'session', True, True),
    ('red', 'blue', 'explicit', True, False),
    ('red', 'blue', 'inferred', False, True),
    ('red', 'red', 'explicit', False, False),
    (None, 'red', 'explicit', True, False),
])
def test_product_constraints_preserve_attribute_types(
    attribute_value, condition_value, source, filterable, expected,
):
    intent = importlib.import_module('kernel.intent')
    constraints = importlib.import_module('kernel.constraints')
    product = ProductView(
        id='product', category_id='category', product_code='code', name='商品',
        brand=None, description=None, attributes={'color': attribute_value},
        status='active', version=1, created_at=READ_AT, updated_at=READ_AT,
    )
    attribute = AttributeView(
        id='attribute', category_id='category', code='color', name='颜色',
        value_type='text', unit=None, is_comparable=False, is_filterable=filterable,
    )
    shopping = intent.ShoppingIntent(hard_conditions=[{
        'code': 'color', 'value': condition_value, 'source': source,
    }])
    assert constraints.matches_product_constraints(
        shopping, product, [attribute],
    ) is expected
    assert not constraints.matches_product_constraints(
        shopping, product.model_copy(update={'status': 'draft'}), [attribute],
    )
    assert not constraints.matches_product_constraints(
        shopping.model_copy(update={'category_id': 'other'}), product, [attribute],
    )


@pytest.mark.parametrize('budget,price,expected', [
    (100, price_view(amount_minor=100), True),
    (100, price_view(amount_minor=101), False),
    (None, price_view(amount_minor=100), True),
    (None, None, False),
    (0, price_view(amount_minor=0), True),
    (100, price_view('USD', 100), True),
])
def test_budget_equality_and_price_tolerance_match_baseline(budget, price, expected):
    intent = importlib.import_module('kernel.intent')
    constraints = importlib.import_module('kernel.constraints')
    assert constraints.matches_budget(
        intent.ShoppingIntent(budget_max_minor=budget), price,
    ) is expected


def test_amount_zero_and_utc_json_match_baseline():
    quotes = importlib.import_module('kernel.quotes')
    from commerce_orchestrator.schemas import CandidatePage, SKUQuote

    assert SKUQuote is quotes.SKUQuote
    assert CandidatePage is quotes.CandidatePage
    quote = quotes.SKUQuote(
        sku_id='sku', price=price_view(amount_minor=0), purchasable=True,
        reason='purchasable', read_at=READ_AT,
    )
    payload = json.loads(quote.model_dump_json())
    assert payload['read_at'] == '2026-10-06T01:02:03Z'
    assert payload['price'] == {
        'sku_id': 'sku', 'currency': 'CNY', 'amount_minor': 0, 'version': 1,
        'effective_at': '2026-10-06T01:02:03Z', 'read_at': '2026-10-06T01:02:03Z',
    }


@pytest.mark.parametrize('modules', [
    ['commerce_orchestrator.contracts', 'commerce_orchestrator.state',
     'cart.schemas', 'catalog.schemas'],
    ['cart.schemas', 'catalog.schemas', 'commerce_orchestrator.state',
     'commerce_orchestrator.contracts'],
    ['catalog.schemas', 'commerce_orchestrator.contracts', 'cart.schemas',
     'commerce_orchestrator.state'],
    ['models.commerce', 'shopping_session.schemas', 'commerce_orchestrator.schemas'],
])
def test_contract_import_orders_resolve_without_placeholders(modules):
    script = f"""
import importlib
from typing import get_type_hints
for name in {modules!r}:
    importlib.import_module(name)
from commerce_orchestrator.contracts import TurnRequest, TurnResponse
from commerce_orchestrator.state import CommerceGraphState
hints = get_type_hints(CommerceGraphState)
assert hints['turn'] is TurnRequest
assert hints['response'] is TurnResponse
"""
    result = subprocess.run(
        [sys.executable, '-c', script], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('dependency', ['config', 'cart', 'catalog.service'])
def test_kernel_stage_rejects_dependencies_outside_schema_whitelist(
    tmp_path, indirect, dependency,
):
    import tomllib

    from tests.test_layering import (
        STAGE_CONFIG,
        run_stage_imports,
        write_stage_module_stubs,
    )

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in [*options['tool']['importlinter']['root_packages'], 'kernel']:
        directory = tmp_path / package
        directory.mkdir(exist_ok=True)
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ['catalog', 'pricing', 'inventory']:
        for module in ['schemas', 'service', 'models']:
            (tmp_path / package / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    (tmp_path / 'kernel' / '__init__.py').write_text(
        'from . import helper\n' if indirect else f'import {dependency}\n',
        encoding='utf-8',
    )
    if indirect:
        (tmp_path / 'kernel' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('package', ['catalog', 'pricing', 'inventory'])
def test_base_schema_stage_rejects_reverse_kernel_dependency(tmp_path, package):
    import tomllib

    from tests.test_layering import (
        STAGE_CONFIG,
        run_stage_imports,
        write_stage_module_stubs,
    )

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for name in [*options['tool']['importlinter']['root_packages'], 'kernel']:
        directory = tmp_path / name
        directory.mkdir(exist_ok=True)
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for name in ['catalog', 'pricing', 'inventory']:
        (tmp_path / name / 'models.py').write_text('', encoding='utf-8')
        (tmp_path / name / 'schemas.py').write_text(
            'import kernel\n' if name == package else '', encoding='utf-8',
        )
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)
