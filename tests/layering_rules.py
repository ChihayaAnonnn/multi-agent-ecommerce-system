"""迁移前角色清单、静态公共入口检查与真实 import-linter 契约生成。"""

from __future__ import annotations

import ast
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

REFLECTION_FUNCTIONS = {'getattr', 'setattr', 'vars'}
PUBLIC = {'service', 'schemas', 'ports', 'types'}
EXCLUDED_DIRECTORIES = {
    'tests', 'frontend', 'demo', 'docs', 'ecommerce-agent-references',
    'node_modules', '__pycache__',
}


@dataclass(frozen=True)
class LayoutTables:
    """单一布局的角色映射、登记清单与精确边白名单。"""

    label: str
    business: frozenset[str]
    legacy: frozenset[str]
    application: frozenset[str]
    application_facts: frozenset[str]
    sessions: frozenset[str]
    facts: frozenset[str]
    http_contracts: frozenset[str]
    composition: frozenset[str]
    adapters: frozenset[str]
    adapter_prefixes: frozenset[str]
    stack_targets: frozenset[str]
    prefix_roles: tuple[tuple[str, str], ...]
    aliases: frozenset[str]
    api_modules: frozenset[str]
    modules: dict[str, str]
    exact_edges: frozenset[tuple[str, str]]
    private_aliases: frozenset[tuple[str, str, str]]
    kernel_schemas: frozenset[str]
    kernel_edges: frozenset[tuple[str, str]]
    cart: str
    checkout: str
    checkout_service: str
    checkout_gateway: str
    kernel_ports: tuple[str, ...]
    model_adapter: str
    orchestrator: str
    orchestrator_graph: str

    @property
    def roots(self) -> frozenset[str]:
        """登记清单里出现的顶层包名，用于判断外部目标是否属于本工程。"""
        return frozenset(name.split('.')[0] for name in self.modules)

    @property
    def registered(self) -> frozenset[str]:
        return frozenset({'main'} | set(self.modules) | {
            f'{package}.{name}'
            for package, names in self.modules.items() for name in names.split()
        })

    @property
    def kernel_packages(self) -> frozenset[str]:
        """基础 schemas 的各级父包，kernel 可以经由它们抵达白名单 schemas。"""
        return frozenset(
            '.'.join(name.split('.')[:count])
            for name in self.kernel_schemas
            for count in range(1, len(name.split('.')))
        )

    @property
    def protected(self) -> frozenset[str]:
        return self.adapters | self.adapter_prefixes

    def role(self, module: str) -> str:
        if module in self.composition:
            return 'composition'
        if module in self.adapters:
            return 'infra'
        if module in self.aliases:
            return 'alias'
        if module in self.api_modules:
            return 'api'
        for prefix, role in self.prefix_roles:
            if module == prefix or module.startswith(prefix + '.'):
                return role
        return module.split('.')[0]


ORIGINAL_BUSINESS = {
    'cart', 'catalog', 'checkout', 'commerce_events', 'identity', 'inventory',
    'pricing', 'sale_facts', 'shopping_session', 'storefront',
}
ORIGINAL_LEGACY = {'agents', 'orchestrator', 'services', 'models'}
ORIGINAL_ADAPTERS = {
    'db.session', 'checkout.gateway', 'commerce_orchestrator.llm_adapter',
}
ORIGINAL_MODULES = {
    'agents': 'base_agent inventory_agent marketing_copy_agent product_rec_agent '
              'user_profile_agent',
    'api': 'auth catalog commerce commerce_demo commerce_read errors experiments '
           'health http_config identity metrics recommendations runtime storefront',
    'cart': 'models schemas service',
    'catalog': 'legacy_adapter models ports schemas seed service',
    'checkout': 'gateway models ports schemas service',
    'commerce_events': 'models schemas service',
    'commerce_orchestrator': 'actions compare contracts discovery facts graph guards '
                             'intent llm_adapter model nodes ports schemas service '
                             'state utils',
    'config': 'settings',
    'data': 'download_amazon_metadata import_amazon_metadata',
    'db': 'base session',
    'identity': 'csrf models passwords rate_limit service tokens types',
    'infrastructure': 'milvus_client mysql_client redis_client',
    'inventory': 'models schemas seed service',
    'kernel': 'constraints errors intent quotes',
    'models': 'commerce schemas',
    'orchestrator': 'graph supervisor',
    'pricing': 'models schemas seed service',
    'sale_facts': 'service',
    'services': 'ab_test feature_store metrics',
    'shared': 'db errors hashing ids principal time',
    'shopping_session': 'models schemas service',
    'storefront': 'schemas service',
    'scripts': 'reset_demo_password',
    'migrations': 'env versions versions.0001_catalog versions.0002_pricing '
                  'versions.0003_inventory versions.0004_identity '
                  'versions.0005_commerce_sessions versions.0006_cart '
                  'versions.0007_checkout versions.0008_commerce_events',
}
ORIGINAL_EXACT_EDGES = {
    ('db', 'db.session'),
    ('commerce_orchestrator.model', 'commerce_orchestrator.llm_adapter'),
    ('catalog.legacy_adapter', 'models.schemas'),
    ('api.commerce', 'models.commerce'),
    ('api.recommendations', 'models.schemas'),
    ('scripts.reset_demo_password', 'db.session'),
    ('scripts.reset_demo_password', 'identity.passwords'),
    ('data.import_amazon_metadata', 'catalog.models'),
    ('data.import_amazon_metadata', 'catalog.seed'),
    ('data.import_amazon_metadata', 'inventory.models'),
    ('data.import_amazon_metadata', 'inventory.seed'),
    ('data.import_amazon_metadata', 'pricing.seed'),
    ('data.import_amazon_metadata', 'db.session'),
} | {('migrations.env', f'{module}.models') for module in ORIGINAL_BUSINESS}
ORIGINAL_KERNEL_SCHEMAS = {'catalog.schemas', 'pricing.schemas', 'inventory.schemas'}
ORIGINAL_KERNEL_EDGES = {
    ('kernel.constraints', 'catalog.schemas'),
    ('kernel.constraints', 'pricing.schemas'),
    ('kernel.quotes', 'catalog.schemas'),
    ('kernel.quotes', 'pricing.schemas'),
    ('kernel.quotes', 'inventory.schemas'),
}

GROUPED_BUSINESS = {
    'modules.cart', 'modules.catalog', 'modules.checkout', 'modules.events',
    'modules.identity', 'modules.inventory', 'modules.pricing',
    'modules.sale_facts', 'modules.sessions',
}
GROUPED_LEGACY = {'legacy'}
GROUPED_APPLICATION = {'application'}
GROUPED_ADAPTERS = {'infra.checkout_gateway', 'infra.db', 'infra.llm'}
GROUPED_MODULES = {
    'api': 'contracts deps errors http_config middleware routes',
    'api.contracts': 'auth commerce',
    'api.routes': 'auth catalog commerce demo health legacy_experiments '
                  'legacy_recommendations metrics storefront',
    'application': 'shopping storefront',
    'application.shopping': 'actions compare contracts discovery facts graph guards '
                            'intent nodes orchestrator ports schemas state',
    'application.storefront': 'schemas service',
    'config': 'settings',
    'data': 'download_amazon_metadata import_amazon_metadata',
    'infra': 'checkout_gateway db llm',
    'kernel': 'constraints errors intent quotes',
    'legacy': 'agents catalog_adapter infrastructure models orchestrator services',
    'legacy.agents': 'base_agent inventory_agent marketing_copy_agent '
                     'product_rec_agent user_profile_agent',
    'legacy.infrastructure': 'milvus_client mysql_client redis_client',
    'legacy.models': 'schemas',
    'legacy.orchestrator': 'graph supervisor',
    'legacy.services': 'ab_test feature_store metrics',
    'migrations': 'env versions versions.0001_catalog versions.0002_pricing '
                  'versions.0003_inventory versions.0004_identity '
                  'versions.0005_commerce_sessions versions.0006_cart '
                  'versions.0007_checkout versions.0008_commerce_events',
    'modules': 'cart catalog checkout events identity inventory pricing '
               'sale_facts sessions',
    'modules.cart': 'models schemas service',
    'modules.catalog': 'models ports schemas seed service',
    'modules.checkout': 'models ports schemas service',
    'modules.events': 'models schemas service',
    'modules.identity': 'csrf models passwords rate_limit service tokens types',
    'modules.inventory': 'models schemas seed service',
    'modules.pricing': 'models schemas seed service',
    'modules.sale_facts': 'service',
    'modules.sessions': 'models schemas service',
    'scripts': 'reset_demo_password',
    'shared': 'db errors hashing ids principal time',
}
GROUPED_EXACT_EDGES = {
    ('legacy.catalog_adapter', 'legacy.models.schemas'),
    ('api.routes.commerce', 'api.contracts.commerce'),
    ('api.routes.legacy_recommendations', 'legacy.models.schemas'),
    ('scripts.reset_demo_password', 'infra.db'),
    ('scripts.reset_demo_password', 'modules.identity.passwords'),
    ('data.import_amazon_metadata', 'modules.catalog.models'),
    ('data.import_amazon_metadata', 'modules.catalog.seed'),
    ('data.import_amazon_metadata', 'modules.inventory.models'),
    ('data.import_amazon_metadata', 'modules.inventory.seed'),
    ('data.import_amazon_metadata', 'modules.pricing.seed'),
    ('data.import_amazon_metadata', 'infra.db'),
} | {('migrations.env', f'{module}.models') for module in GROUPED_BUSINESS}
GROUPED_KERNEL_SCHEMAS = {
    'modules.catalog.schemas', 'modules.inventory.schemas', 'modules.pricing.schemas',
}
GROUPED_KERNEL_EDGES = {
    ('kernel.constraints', 'modules.catalog.schemas'),
    ('kernel.constraints', 'modules.pricing.schemas'),
    ('kernel.quotes', 'modules.catalog.schemas'),
    ('kernel.quotes', 'modules.pricing.schemas'),
    ('kernel.quotes', 'modules.inventory.schemas'),
}

ORIGINAL_TABLES = LayoutTables(
    label='original',
    business=frozenset(ORIGINAL_BUSINESS),
    legacy=frozenset(ORIGINAL_LEGACY),
    application=frozenset({'commerce_orchestrator'}),
    application_facts=frozenset({'commerce_orchestrator'}),
    sessions=frozenset({'shopping_session'}),
    facts=frozenset({'cart', 'storefront', 'sale_facts'}),
    http_contracts=frozenset({'models.commerce'}),
    composition=frozenset({'main', 'api.runtime'}),
    adapters=frozenset(ORIGINAL_ADAPTERS),
    adapter_prefixes=frozenset({'infrastructure'}),
    stack_targets=frozenset(
        ORIGINAL_ADAPTERS | {'infrastructure', 'db', 'config'} | ORIGINAL_LEGACY
    ),
    prefix_roles=(
        ('infrastructure', 'infra'),
        ('commerce_orchestrator', 'application'),
        ('scripts', 'tool'),
        ('data', 'tool'),
        ('migrations', 'tool'),
    ) + tuple((name, 'business') for name in sorted(ORIGINAL_BUSINESS)) + tuple(
        (name, 'legacy') for name in sorted(ORIGINAL_LEGACY)
    ),
    aliases=frozenset(
        {'catalog.legacy_adapter', 'commerce_orchestrator.model', 'db', 'db.base'}
    ),
    api_modules=frozenset({'models.commerce'}),
    modules=ORIGINAL_MODULES,
    exact_edges=frozenset(ORIGINAL_EXACT_EDGES),
    private_aliases=frozenset({
        ('identity.types', 'shared.principal', '_length_prefixed'),
    }),
    kernel_schemas=frozenset(ORIGINAL_KERNEL_SCHEMAS),
    kernel_edges=frozenset(ORIGINAL_KERNEL_EDGES),
    cart='cart',
    checkout='checkout',
    checkout_service='checkout.service',
    checkout_gateway='checkout.gateway',
    kernel_ports=('commerce_orchestrator.ports', 'commerce_orchestrator.guards'),
    model_adapter='commerce_orchestrator.llm_adapter',
    orchestrator='commerce_orchestrator.service',
    orchestrator_graph='commerce_orchestrator.graph',
)

GROUPED_TABLES = LayoutTables(
    label='grouped',
    business=frozenset(GROUPED_BUSINESS),
    legacy=frozenset(GROUPED_LEGACY),
    application=frozenset(GROUPED_APPLICATION),
    application_facts=frozenset({'application.shopping'}),
    sessions=frozenset({'modules.sessions'}),
    facts=frozenset({'modules.cart', 'application.storefront', 'modules.sale_facts'}),
    http_contracts=frozenset({'api.contracts.commerce'}),
    composition=frozenset({'main', 'api.deps'}),
    adapters=frozenset(GROUPED_ADAPTERS),
    # 旧栈的 MySQL/Redis/Milvus 连接封装迁到 legacy/infrastructure/ 后
    # 仍须受 protected 契约约束：只有组合根可以直接导入。
    adapter_prefixes=frozenset({'legacy.infrastructure'}),
    stack_targets=frozenset(GROUPED_ADAPTERS | {'config', 'legacy'}),
    prefix_roles=(
        ('modules', 'business'),
        ('application', 'application'),
        ('kernel', 'kernel'),
        ('shared', 'shared'),
        ('infra', 'infra'),
        ('legacy', 'legacy'),
        ('api', 'api'),
        ('config', 'config'),
        ('data', 'tool'),
        ('scripts', 'tool'),
        ('migrations', 'tool'),
    ),
    aliases=frozenset(),
    api_modules=frozenset(),
    modules=GROUPED_MODULES,
    exact_edges=frozenset(GROUPED_EXACT_EDGES),
    private_aliases=frozenset({
        ('modules.identity.types', 'shared.principal', '_length_prefixed'),
    }),
    kernel_schemas=frozenset(GROUPED_KERNEL_SCHEMAS),
    kernel_edges=frozenset(GROUPED_KERNEL_EDGES),
    cart='modules.cart',
    checkout='modules.checkout',
    checkout_service='modules.checkout.service',
    checkout_gateway='infra.checkout_gateway',
    kernel_ports=('application.shopping.ports', 'application.shopping.guards'),
    model_adapter='infra.llm',
    orchestrator='application.shopping.orchestrator',
    orchestrator_graph='application.shopping.graph',
)

_LAYOUTS = {'original': ORIGINAL_TABLES, 'grouped': GROUPED_TABLES}


def _layout_tables(layout: str) -> LayoutTables:
    """取用布局常量；未启用的布局必须显式失败而不是静默降级。"""
    if layout == 'packaged':
        raise ValueError(
            f'layout {layout!r} 尚未启用；Task 15 前仅支持 original 与 grouped'
        )
    try:
        return _LAYOUTS[layout]
    except KeyError:
        raise ValueError(
            f'未知布局 {layout!r}；仅支持 {sorted(_LAYOUTS)}'
        ) from None


def _owner(module: str, packages: frozenset[str]) -> str | None:
    """返回 module 所属的最长业务包名；不属于任何业务包时返回 None。"""
    for package in sorted(packages, key=len, reverse=True):
        if module == package or module.startswith(package + '.'):
            return package
    return None


def _boundary(module: str, tables: LayoutTables) -> str:
    """跨边界判定用的归属名：业务模块归其业务包，其余归顶层包。"""
    return _owner(module, tables.business) or module.split('.')[0]


def _graph_visible(module: str, modules: dict[str, Path | None]) -> bool:
    """模块能否进入 grimp 导入图：常规包内部的命名空间子目录会被跳过。

    `grimp` 遍历到已含 `__init__.py` 的常规包后，要求其下每一层目录也有
    `__init__.py`，否则整棵子树不进入导入图（`api/contracts/` 就是这种情况）。
    顶层命名空间包（`data`/`scripts`/`migrations`）不受影响。
    """
    parts = module.split('.')
    for count in range(1, len(parts)):
        ancestor = '.'.join(parts[:count])
        if modules.get(ancestor) is not None:
            continue
        if count == 1:
            return True
        if modules.get('.'.join(parts[:count - 1])) is not None:
            return False
    return True


def _inventory(root: Path, layout: str) -> dict[str, Path | None]:
    _layout_tables(layout)
    modules: dict[str, Path | None] = {}
    for path in sorted(root.rglob('*.py')):
        relative = path.relative_to(root)
        if any(part.startswith('.') or part in EXCLUDED_DIRECTORIES
               for part in relative.parts):
            continue
        parts = list(relative.with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        if not parts:
            continue
        module = '.'.join(parts)
        for count in range(1, len(parts)):
            modules.setdefault('.'.join(parts[:count]), None)
        modules[module] = path
    return modules


@dataclass(frozen=True)
class Import:
    source: str
    target: str
    symbol: str | None
    local: str
    line: int


def _imports(module: str, path: Path, tree: ast.Module,
             modules: dict[str, Path | None]) -> tuple[list[Import], list[str]]:
    imports = []
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(Import(module, alias.name, None,
                                      alias.asname or alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ''
            if node.level:
                package = module if path.name == '__init__.py' else (
                    module.rpartition('.')[0]
                )
                parts = package.split('.') if package else []
                if node.level > len(parts):
                    issues.append(f'{module}:{node.lineno}: unresolved relative import')
                    continue
                prefix = '.'.join(parts[:len(parts) - node.level + 1]
                                  + ([prefix] if prefix else []))
            for alias in node.names:
                child = f'{prefix}.{alias.name}'
                target, symbol = (child, None) if child in modules else (
                    prefix, alias.name
                )
                imports.append(Import(module, target, symbol,
                                      alias.asname or alias.name, node.lineno))
    return imports, issues


def _inventory_issues(root: Path, modules: dict[str, Path | None],
                      tables: LayoutTables) -> list[str]:
    issues = [f'unregistered module: {module}'
              for module in sorted(modules.keys() - tables.registered)]
    configuration = root / 'pyproject.toml'
    if configuration.exists():
        options = tomllib.loads(configuration.read_text(encoding='utf-8'))
        layering = options.get('tool', {}).get('layering', {})
        if layering.get('require_complete_inventory'):
            issues += [f'missing registered module: {module}'
                       for module in sorted(tables.registered - modules.keys())]
    if not modules:
        issues.append('missing application modules')
    return issues


def _exports(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == '__all__'
            for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
            if isinstance(value, (list, tuple)) and all(
                isinstance(name, str) for name in value
            ):
                return set(value)
    return None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f'{parent}.{node.attr}' if parent else None
    return None


def _origins(module: str, symbol: str, bindings: dict,
             seen: frozenset = frozenset()) -> set[tuple[str, str]]:
    key = (module, symbol)
    if key in seen:
        return {key}
    sources = bindings.get(module, {}).get(symbol)
    if sources:
        origins = set()
        for source in sources:
            origins.update(_origins(*source, bindings, seen | {key}))
        return origins
    return {key}


def _value_origins(module: str, node: ast.AST | None,
                   bindings: dict) -> set[tuple[str, str]]:
    dotted = _dotted(node) if node is not None else None
    if not dotted:
        return set()
    for alias in sorted(bindings[module], key=len, reverse=True):
        if dotted == alias:
            return _origins(module, alias, bindings)
        if dotted.startswith(alias + '.'):
            suffix = dotted[len(alias) + 1:]
            return {(origin, f'{symbol}.{suffix}' if symbol else suffix)
                    for origin, symbol in _origins(module, alias, bindings)}
    return set()


def _contract(name: str, sources: list[str], targets: list[str],
              *, direct: bool = False, ignores: list[str] | None = None) -> dict:
    result = {'name': name, 'type': 'forbidden', 'source_modules': sources,
              'forbidden_modules': targets}
    if direct:
        result['allow_indirect_imports'] = True
    if ignores:
        result['ignore_imports'] = ignores
        result['unmatched_ignore_imports_alerting'] = 'none'
    return result


def _contracts(modules: dict[str, Path | None],
               tables: LayoutTables) -> list[dict]:
    roots = sorted(module for module in modules
                   if '.' not in module and module != 'main')
    # 只按 grimp 可见性过滤，不能再叠加「必须有 __init__.py」的条件：
    # 顶层命名空间包（data/scripts/migrations）本就没有 __init__.py，
    # 叠加后会把它们从 shared/kernel 的 forbidden 集合里静默剔除，削弱契约。
    visible = {module for module in modules if _graph_visible(module, modules)}
    contracts = []

    def add(name, sources, targets, **options):
        present_sources = sorted(set(sources) & visible)
        present_targets = sorted(set(targets) & visible)
        if present_sources and present_targets:
            contracts.append(_contract(name, present_sources, present_targets,
                                       **options))

    add('业务与应用能力不得依赖 HTTP 层',
        tables.business | tables.application, ['api'])
    add('shared 不得依赖配置或业务包', ['shared'], set(roots) - {'shared'})
    add('kernel 只允许基础 schemas 与 shared', ['kernel'],
        set(roots) - {'kernel', 'shared'}, ignores=[
            f'{source} -> {target}' for source, target in sorted(tables.kernel_edges)
            if source in modules and target in modules
        ])
    add('基础 schemas 不得反向依赖 kernel', tables.kernel_schemas, ['kernel'])
    add('sessions 不得依赖应用响应契约', tables.sessions,
        sorted(tables.application | tables.http_contracts))
    add('业务事实能力不得依赖应用事实读取', tables.facts,
        sorted(tables.application_facts))
    add('cart 不得协调会话和应用层', [tables.cart],
        sorted(tables.sessions | tables.application))
    add('checkout 不得协调会话和应用层', [tables.checkout],
        sorted(tables.sessions | tables.application))
    add('checkout 业务服务不得依赖宿主适配器', [tables.checkout_service],
        [tables.checkout_gateway])
    add('模型端口与 guards 不得依赖具体模型适配器',
        list(tables.kernel_ports), [tables.model_adapter])
    add('orchestrator 不得构造具体图与模型适配器',
        [tables.orchestrator], [tables.orchestrator_graph, tables.model_adapter])
    inner = [module for module in modules if module in visible and '.' in module and
             tables.role(module) in {'business', 'application'}]
    add('模块与应用不得间接依赖具体基础设施或旧栈', inner,
        sorted(tables.stack_targets))
    for package in sorted(tables.business):
        add(f'{package} 不得直接访问外部 ORM', [package],
            {f'{other}.models' for other in tables.business - {package}},
            direct=True)
    for source in sorted(modules):
        if source not in visible:
            # 命名空间父包没有自己的代码，且 grimp 看不到常规包内部的
            # 命名空间子目录，逐模块契约只覆盖真正进入导入图的模块。
            continue
        if tables.role(source) in {'api', 'application', 'infra', 'legacy'} and (
            '.' in source
        ):
            add(f'{source} 不得直接访问业务 ORM', [source],
                {f'{package}.models' for package in tables.business}, direct=True)
    protected = sorted(tables.protected & visible)
    if protected:
        contracts.append({
            'name': '具体适配器仅组合根与精确兼容入口可以直接导入',
            'type': 'protected', 'protected_modules': protected,
            'allowed_importers': sorted(
                (tables.composition - {'main'}) & modules.keys()
            ),
            'ignore_imports': [f'{source} -> {target}'
                               for source, target in sorted(tables.exact_edges)
                               if target in protected and source in modules],
            'unmatched_ignore_imports_alerting': 'none',
        })
    return contracts


def generate_import_contracts(root: Path, layout: str) -> str:
    """生成当前真实清单的完整 TOML；未登记模块必须先经过角色审核。"""
    tables = _layout_tables(layout)
    modules = _inventory(root, layout)
    issues = _inventory_issues(root, modules, tables)
    for module, path in modules.items():
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports, errors = _imports(module, path, tree, modules)
        issues.extend(errors)
        issues.extend(f'{module}: missing target {item.target}' for item in imports
                      if item.target.split('.')[0] in tables.roots
                      and item.target not in modules)
    if issues:
        raise ValueError('\n'.join(issues))
    options = {
        'root_packages': sorted(module for module in modules
                                if '.' not in module and module != 'main'),
        'include_namespace_packages': True,
    }
    lines = ['[tool.importlinter]']
    for key, value in options.items():
        lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
    for contract in _contracts(modules, tables):
        lines += ['', '[[tool.importlinter.contracts]]']
        for key, value in contract.items():
            lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
    lines += ['', '[tool.layering.inventory]']
    for module in sorted(modules):
        lines.append(f'{json.dumps(module)} = {json.dumps(tables.role(module))}')
    return '\n'.join(lines) + '\n'


def check_import_boundaries(root: Path, layout: str) -> list[str]:
    """静态检查函数内、条件、相对导入与公共符号，不执行目标源码。"""
    tables = _layout_tables(layout)
    modules = _inventory(root, layout)
    issues = _inventory_issues(root, modules, tables)
    if not modules:
        return issues
    trees = {module: ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
             for module, path in modules.items() if path is not None}
    exports = {module: _exports(tree) for module, tree in trees.items()}
    imports = []
    bindings: dict[str, dict[str, set[tuple[str, str]]]] = {}
    for module, tree in trees.items():
        found, errors = _imports(module, modules[module], tree, modules)
        imports.extend(found)
        issues.extend(errors)
        bindings[module] = {
            function: {('builtins', function)} for function in REFLECTION_FUNCTIONS
        }
        for item in found:
            bindings[module].setdefault(item.local, set()).add(
                (item.target, item.symbol or ''),
            )
    for module, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            origins = _value_origins(module, node.value, bindings)
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[module].setdefault(target.id, set()).update(origins)
    for module, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = _dotted(node.func)
                if tables.role(module) == 'kernel' and function in {'open', 'input'}:
                    issues.append(f'{module}:{node.lineno}: kernel I/O call')
                function_origins = _value_origins(module, node.func, bindings)
                reflection = any(origin == 'builtins' and name in REFLECTION_FUNCTIONS
                                 for origin, name in function_origins)
                if reflection and node.args:
                    origins = _value_origins(module, node.args[0], bindings)
                    if any(not symbol and origin.split('.')[0] in tables.roots
                           for origin, symbol in origins):
                        issues.append(f'{module}:{node.lineno}: dynamic module access')
            if isinstance(node, ast.Name) and node.id in {'__import__', 'eval', 'exec'}:
                issues.append(f'{module}:{node.lineno}: dynamic import/evaluation')
            if isinstance(node, ast.Attribute) and node.attr in {
                'import_module', '__import__', 'exec_module', 'load_module',
            }:
                issues.append(f'{module}:{node.lineno}: dynamic import')
    for item in imports:
        if item.target.startswith('importlib') or (
            item.target == 'builtins'
            and item.symbol in {'__import__', 'eval', 'exec'}
        ):
            issues.append(f'{item.source}:{item.line}: dynamic import')

    graph = {module: set() for module in modules}

    def public_symbol(source, target, symbol, line):
        if (source, target, symbol) in tables.private_aliases:
            return
        if (symbol.startswith('_') or symbol == '*') and (
            _boundary(source, tables) != _boundary(target, tables)
        ):
            issues.append(f'{source}:{line}: private symbol {target}.{symbol}')
            return
        target_package = _owner(target, tables.business)
        if target_package and _boundary(source, tables) != target_package and (
            (source, target) not in tables.exact_edges
        ) and source not in tables.composition:
            if symbol not in (exports.get(target) or set()):
                issues.append(f'{source}:{line}: not public in __all__: '
                              f'{target}.{symbol}')
        for origin, original_symbol in _origins(target, symbol, bindings):
            if original_symbol.startswith('_') and _boundary(
                source, tables
            ) != _boundary(origin, tables):
                issues.append(f'{source}:{line}: private re-export '
                              f'{origin}.{original_symbol}')
            if _owner(origin, tables.business) and '.models' in origin:
                if _boundary(source, tables) != _boundary(origin, tables):
                    issues.append(f'{source}:{line}: ORM re-export '
                                  f'{origin}.{original_symbol}')
            if origin in tables.adapters and source not in tables.composition:
                issues.append(f'{source}:{line}: adapter re-export {origin}')

    for item in imports:
        source, target = item.source, item.target
        target_root = target.split('.')[0]
        role = tables.role(source)
        if target_root in tables.roots or target_root == 'main':
            if target not in modules:
                issues.append(f'{source}:{item.line}: missing target {target}')
            else:
                graph[source].add(target)
        if role in {'business', 'application', 'kernel', 'shared'}:
            forbidden_external = {'fastapi', 'starlette'}
            if role == 'kernel':
                forbidden_external |= {'sqlalchemy', 'redis', 'httpx', 'requests',
                                       'langchain_openai', 'socket', 'subprocess'}
            if target_root in forbidden_external:
                issues.append(f'{source}:{item.line}: forbidden dependency {target}')
        if (source, target) in tables.exact_edges:
            continue
        if role in {'api', 'infra'} and tables.role(target) == 'legacy':
            issues.append(f'{source}:{item.line}: forbidden legacy dependency {target}')
        if target in tables.adapters or target_root in tables.adapter_prefixes:
            if source not in tables.composition and role != 'infra':
                issues.append(f'{source}:{item.line}: concrete adapter {target}')
        target_package = _owner(target, tables.business)
        if target_package and _boundary(source, tables) != target_package:
            if source not in tables.composition and (
                target.rsplit('.', 1)[-1] not in PUBLIC
            ):
                issues.append(f'{source}:{item.line}: internal module {target}')
        if (role == 'kernel' and _owner(target, tables.business)
                and target not in tables.kernel_schemas):
            issues.append(f'{source}:{item.line}: kernel dependency {target}')
        if item.symbol is not None:
            public_symbol(source, target, item.symbol, item.line)

    for source, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            for origin, symbol in _value_origins(source, node.value, bindings):
                if not symbol:
                    public_symbol(source, origin, node.attr, node.lineno)
        for symbol in exports[source] or set():
            for origin, original_symbol in _origins(source, symbol, bindings):
                if _owner(origin, tables.business) and '.models' in origin and (
                    source.rsplit('.', 1)[-1] != 'models'
                ):
                    issues.append(f'{source}: ORM re-export {origin}.{original_symbol}')

    for contract in _contracts(modules, tables):
        if contract['type'] != 'forbidden':
            continue
        ignored = set(contract.get('ignore_imports', []))
        targets = contract['forbidden_modules']
        for source in graph:
            if not any(source == parent or source.startswith(parent + '.')
                       for parent in contract['source_modules']):
                continue
            visited = {source}
            pending = [source]
            while pending:
                current = pending.pop()
                for target in graph[current]:
                    if f'{current} -> {target}' in ignored:
                        continue
                    if any(target == parent or target.startswith(parent + '.')
                           for parent in targets):
                        issues.append(f'{source}: {contract["name"]}: {target}')
                    if (not contract.get('allow_indirect_imports')
                            and target not in visited):
                        visited.add(target)
                        pending.append(target)
    runtime_graph = {module: dependencies | {
        module.rpartition('.')[0]
    } if '.' in module else dependencies for module, dependencies in graph.items()}
    for source in runtime_graph:
        role = tables.role(source)
        if role not in {'business', 'application', 'kernel', 'shared'}:
            continue
        visited = {source}
        pending = [source]
        while pending:
            current = pending.pop()
            for target in runtime_graph[current]:
                if target in visited:
                    continue
                visited.add(target)
                pending.append(target)
                target_role = tables.role(target)
                forbidden = target_role in {
                    'composition', 'infra', 'alias', 'api', 'legacy', 'config', 'tool',
                }
                if role == 'shared':
                    forbidden = target_role != 'shared'
                if source in tables.kernel_schemas and target_role == 'kernel':
                    forbidden = True
                if role == 'kernel' and target_role == 'business':
                    forbidden = target not in tables.kernel_schemas and (
                        target not in tables.kernel_packages
                    )
                if forbidden:
                    issues.append(f'{source}: forbidden indirect dependency {target}')
    return sorted(set(issues))
