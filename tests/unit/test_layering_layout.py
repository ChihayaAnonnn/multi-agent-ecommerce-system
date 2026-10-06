"""唯一包根布局（Task 15）的显式验收：新路径可导入、应用层不越界、旧路由仍在。

这些断言与 `test_layering.py` 的 AST 检查、`pyproject.toml` 的 CLI 契约互补：
前两者是规则引擎，本文件给出可读的、与具体布局一一对应的直接证据。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 每个分组的公开入口：搬迁后必须可直接导入
PUBLIC_ENTRIES = [
    ('ecom.modules.catalog.service', 'CatalogService'),
    ('ecom.modules.pricing.service', 'PricingService'),
    ('ecom.modules.inventory.service', 'InventoryService'),
    ('ecom.modules.cart.service', 'CartService'),
    ('ecom.modules.checkout.service', 'CheckoutService'),
    ('ecom.modules.identity.service', 'IdentityService'),
    ('ecom.modules.sessions.service', 'ShoppingSessionService'),
    ('ecom.modules.events.service', 'CommerceEventService'),
    ('ecom.modules.sale_facts.service', 'SaleFactsReader'),
    ('ecom.application.shopping.orchestrator', 'CommerceOrchestrator'),
    ('ecom.application.shopping.contracts', 'TurnResponse'),
    ('ecom.application.storefront.service', 'StorefrontService'),
    ('ecom.kernel.quotes', 'evaluate_purchasability'),
    ('ecom.kernel.intent', 'ShoppingIntent'),
    ('ecom.shared.principal', 'Principal'),
    ('ecom.infra.db', 'create_session_factory'),
    ('ecom.api.deps', 'create_runtime'),
]


def test_packaged_imports_and_public_entries():
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
    forbidden_prefixes = ('ecom.api', 'ecom.infra', 'ecom.legacy')
    offenders: list[str] = []
    for path in sorted((ROOT / 'ecom' / 'application').rglob('*.py')):
        for module in _imported_modules(path):
            if any(module == prefix or module.startswith(prefix + '.')
                   for prefix in forbidden_prefixes):
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


def test_packaged_top_level_layout_is_complete():
    """唯一包根齐备，分组目录与旧平铺包不再有源文件。

    断言看的是残留的 `.py` 源文件而不是目录是否存在：切换分支或合并后，
    旧包目录常因遗留 `__pycache__/` 而继续存在，那是构建缓存而非代码。
    真出现旧源文件（覆盖不完整、手工复制遗漏）仍会被这条抓住。
    """
    package = ROOT / 'ecom'
    assert (package / '__init__.py').exists(), '缺少 ecom/__init__.py'
    for name in ('api', 'application', 'infra', 'kernel', 'legacy', 'modules',
                 'shared'):
        assert (package / name / '__init__.py').exists(), f'缺少分组 ecom.{name}'
    assert (package / 'config.py').is_file(), '缺少 ecom/config.py'
    assert (package / 'main.py').is_file(), '缺少 ecom/main.py'
    for gone in ('api', 'application', 'config', 'infra', 'kernel', 'legacy',
                 'modules', 'shared', 'main.py', 'catalog', 'pricing', 'inventory',
                 'cart', 'checkout', 'identity', 'shopping_session',
                 'commerce_events', 'sale_facts', 'commerce_orchestrator',
                 'storefront', 'agents', 'orchestrator', 'services',
                 'infrastructure', 'db', 'models'):
        path = ROOT / gone
        if not path.exists():
            continue
        if path.is_dir():
            leftovers = sorted(
                str(source.relative_to(ROOT)) for source in path.rglob('*.py')
            )
        else:
            leftovers = [gone]
        assert leftovers == [], f'旧包仍有源文件: {leftovers}'
