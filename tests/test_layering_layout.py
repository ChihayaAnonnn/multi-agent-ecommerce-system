"""分组布局（Task 14）的显式验收：新路径可导入、应用层不越界、旧路由仍在。

这些断言与 `test_layering.py` 的 AST 检查、`.importlinter.stage.toml` 的 CLI 契约互补：
前两者是规则引擎，本文件给出可读的、与具体布局一一对应的直接证据。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 每个分组的公开入口：搬迁后必须可直接导入
PUBLIC_ENTRIES = [
    ('modules.catalog.service', 'CatalogService'),
    ('modules.pricing.service', 'PricingService'),
    ('modules.inventory.service', 'InventoryService'),
    ('modules.cart.service', 'CartService'),
    ('modules.checkout.service', 'CheckoutService'),
    ('modules.identity.service', 'IdentityService'),
    ('modules.sessions.service', 'ShoppingSessionService'),
    ('modules.events.service', 'CommerceEventService'),
    ('modules.sale_facts.service', 'SaleFactsReader'),
    ('application.shopping.orchestrator', 'CommerceOrchestrator'),
    ('application.shopping.contracts', 'TurnResponse'),
    ('application.storefront.service', 'StorefrontService'),
    ('kernel.quotes', 'evaluate_purchasability'),
    ('kernel.intent', 'ShoppingIntent'),
    ('shared.principal', 'Principal'),
    ('infra.db', 'create_session_factory'),
    ('api.deps', 'create_runtime'),
]


def test_grouped_imports_and_public_entries():
    import importlib

    for module_name, symbol in PUBLIC_ENTRIES:
        module = importlib.import_module(module_name)
        assert hasattr(module, symbol), f'{module_name} 缺少公开入口 {symbol}'


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def test_application_has_no_api_or_infra_dependency():
    """应用层只接收注入，不得直接依赖接口层、基础设施实现或旧栈。"""
    forbidden_prefixes = ('api', 'infra', 'legacy')
    offenders: list[str] = []
    for path in sorted((ROOT / 'application').rglob('*.py')):
        for module in _imported_modules(path):
            if module.split('.')[0] in forbidden_prefixes:
                offenders.append(f'{path.relative_to(ROOT)} -> {module}')
    assert offenders == []


def test_legacy_routes_are_still_registered():
    """按 D1/D2 保留的旧接口必须仍在路由表中，且与冻结契约一致。"""
    contract = ROOT / 'tests' / 'fixtures' / 'layering' / 'routes.json'
    recorded = json.loads(contract.read_text(encoding='utf-8'))
    registered = {entry['path'] for entry in recorded}
    for path in (
        '/api/v1/recommend',
        '/api/v1/recommend/graph',
        '/api/v1/experiments',
        '/api/v1/metrics',
    ):
        assert path in registered, f'保留的旧路由 {path} 未注册'


def test_grouped_top_level_layout_is_complete():
    """顶层分组目录齐备，且旧的平铺包已不存在。"""
    expected = {'api', 'application', 'infra', 'kernel', 'legacy', 'modules', 'shared'}
    present = {
        p.name for p in ROOT.iterdir()
        if p.is_dir() and (p / '__init__.py').exists()
    }
    assert expected <= present
    for gone in ('catalog', 'pricing', 'inventory', 'cart', 'checkout',
                 'identity', 'shopping_session', 'commerce_events', 'sale_facts',
                 'commerce_orchestrator', 'storefront', 'agents', 'orchestrator',
                 'services', 'infrastructure', 'db', 'models'):
        assert not (ROOT / gone).exists(), f'旧平铺包 {gone}/ 仍然存在'
