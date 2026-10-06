"""迁移前角色清单、静态公共入口检查与真实 import-linter 契约生成。"""

from __future__ import annotations

import ast
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

BUSINESS = {
    'cart', 'catalog', 'checkout', 'commerce_events', 'identity', 'inventory',
    'pricing', 'sale_facts', 'shopping_session', 'storefront',
}
LEGACY = {'agents', 'orchestrator', 'services', 'models'}
PUBLIC = {'service', 'schemas', 'ports', 'types'}
REFLECTION_FUNCTIONS = {'getattr', 'setattr', 'vars'}
COMPOSITION = {'main', 'api.runtime'}
ADAPTERS = {'db.session', 'checkout.gateway', 'commerce_orchestrator.llm_adapter'}
MODULES = {
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
REGISTERED = {'main'} | set(MODULES) | {
    f'{package}.{module}'
    for package, modules in MODULES.items() for module in modules.split()
}
EXCLUDED_DIRECTORIES = {
    'tests', 'frontend', 'demo', 'docs', 'ecommerce-agent-references',
    'node_modules', '__pycache__',
}
EXACT_EDGES = {
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
} | {('migrations.env', f'{module}.models') for module in BUSINESS}
PRIVATE_ALIASES = {('identity.types', 'shared.principal', '_length_prefixed')}
KERNEL_SCHEMAS = {'catalog.schemas', 'pricing.schemas', 'inventory.schemas'}
KERNEL_EDGES = {
    ('kernel.constraints', 'catalog.schemas'),
    ('kernel.constraints', 'pricing.schemas'),
    ('kernel.quotes', 'catalog.schemas'),
    ('kernel.quotes', 'pricing.schemas'),
    ('kernel.quotes', 'inventory.schemas'),
}


def _role(module: str) -> str:
    if module in COMPOSITION:
        return 'composition'
    if module in ADAPTERS or module.startswith('infrastructure'):
        return 'infra'
    if module in {'catalog.legacy_adapter', 'commerce_orchestrator.model',
                  'db', 'db.base'}:
        return 'alias'
    if module == 'models.commerce':
        return 'api'
    package = module.split('.')[0]
    if package in BUSINESS:
        return 'business'
    if package in LEGACY:
        return 'legacy'
    if package == 'commerce_orchestrator':
        return 'application'
    if package in {'scripts', 'data', 'migrations'}:
        return 'tool'
    return package


def _inventory(root: Path, layout: str) -> dict[str, Path | None]:
    if layout != 'original':
        raise ValueError(f'layout {layout!r} 尚未启用；D5 前仅支持 original')
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


def _inventory_issues(root: Path, modules: dict[str, Path | None]) -> list[str]:
    issues = [f'unregistered module: {module}'
              for module in sorted(modules.keys() - REGISTERED)]
    configuration = root / 'pyproject.toml'
    if configuration.exists():
        options = tomllib.loads(configuration.read_text(encoding='utf-8'))
        layering = options.get('tool', {}).get('layering', {})
        if layering.get('require_complete_inventory'):
            issues += [f'missing registered module: {module}'
                       for module in sorted(REGISTERED - modules.keys())]
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


def _contracts(modules: dict[str, Path | None]) -> list[dict]:
    roots = sorted(module for module in modules
                   if '.' not in module and module != 'main')
    contracts = []

    def add(name, sources, targets, **options):
        present_sources = sorted(set(sources) & modules.keys())
        present_targets = sorted(set(targets) & modules.keys())
        if present_sources and present_targets:
            contracts.append(_contract(name, present_sources, present_targets,
                                       **options))

    add('业务与应用能力不得依赖 HTTP 层', BUSINESS | {'commerce_orchestrator'}, ['api'])
    add('shared 不得依赖配置或业务包', ['shared'], set(roots) - {'shared'})
    add('kernel 只允许基础 schemas 与 shared', ['kernel'],
        set(roots) - {'kernel', 'shared'}, ignores=[
            f'{source} -> {target}' for source, target in sorted(KERNEL_EDGES)
            if source in modules and target in modules
        ])
    add('基础 schemas 不得反向依赖 kernel', KERNEL_SCHEMAS, ['kernel'])
    add('sessions 不得依赖应用响应契约', ['shopping_session'],
        ['commerce_orchestrator', 'models.commerce'])
    add('业务事实能力不得依赖应用事实读取', ['cart', 'storefront', 'sale_facts'],
        ['commerce_orchestrator'])
    add('cart 不得协调会话和应用层', ['cart'],
        ['shopping_session', 'commerce_orchestrator'])
    add('checkout 不得协调会话和应用层', ['checkout'],
        ['shopping_session', 'commerce_orchestrator'])
    add('checkout 业务服务不得依赖宿主适配器', ['checkout.service'],
        ['checkout.gateway'])
    add('模型端口与 guards 不得依赖具体模型适配器',
        ['commerce_orchestrator.ports', 'commerce_orchestrator.guards'],
        ['commerce_orchestrator.llm_adapter'])
    add('orchestrator 不得构造具体图与模型适配器',
        ['commerce_orchestrator.service'],
        ['commerce_orchestrator.graph', 'commerce_orchestrator.llm_adapter'])
    inner = [module for module in modules if '.' in module and
             _role(module) in {'business', 'application'}]
    add('模块与应用不得间接依赖具体基础设施或旧栈', inner,
        ADAPTERS | {'infrastructure', 'db', 'config'} | LEGACY)
    for package in sorted(BUSINESS):
        add(f'{package} 不得直接访问外部 ORM', [package],
            {f'{other}.models' for other in BUSINESS - {package}}, direct=True)
    for source in sorted(modules):
        if _role(source) in {'api', 'application', 'infra', 'legacy'} and '.' in source:
            add(f'{source} 不得直接访问业务 ORM', [source],
                {f'{package}.models' for package in BUSINESS}, direct=True)
    protected = sorted((ADAPTERS | {'infrastructure'}) & modules.keys())
    if protected:
        contracts.append({
            'name': '具体适配器仅组合根与精确兼容入口可以直接导入',
            'type': 'protected', 'protected_modules': protected,
            'allowed_importers': sorted((COMPOSITION - {'main'}) & modules.keys()),
            'ignore_imports': [f'{source} -> {target}'
                               for source, target in sorted(EXACT_EDGES)
                               if target in protected and source in modules],
            'unmatched_ignore_imports_alerting': 'none',
        })
    return contracts


def generate_import_contracts(root: Path, layout: str) -> str:
    """生成当前真实清单的完整 TOML；未登记模块必须先经过角色审核。"""
    modules = _inventory(root, layout)
    issues = _inventory_issues(root, modules)
    for module, path in modules.items():
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports, errors = _imports(module, path, tree, modules)
        issues.extend(errors)
        issues.extend(f'{module}: missing target {item.target}' for item in imports
                      if item.target.split('.')[0] in MODULES
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
    for contract in _contracts(modules):
        lines += ['', '[[tool.importlinter.contracts]]']
        for key, value in contract.items():
            lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
    lines += ['', '[tool.layering.inventory]']
    for module in sorted(modules):
        lines.append(f'{json.dumps(module)} = {json.dumps(_role(module))}')
    return '\n'.join(lines) + '\n'


def check_import_boundaries(root: Path, layout: str) -> list[str]:
    """静态检查函数内、条件、相对导入与公共符号，不执行目标源码。"""
    modules = _inventory(root, layout)
    issues = _inventory_issues(root, modules)
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
                if _role(module) == 'kernel' and function in {'open', 'input'}:
                    issues.append(f'{module}:{node.lineno}: kernel I/O call')
                function_origins = _value_origins(module, node.func, bindings)
                reflection = any(origin == 'builtins' and name in REFLECTION_FUNCTIONS
                                 for origin, name in function_origins)
                if reflection and node.args:
                    origins = _value_origins(module, node.args[0], bindings)
                    if any(not symbol and origin.split('.')[0] in MODULES
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
        if (source, target, symbol) in PRIVATE_ALIASES:
            return
        if (symbol.startswith('_') or symbol == '*') and (
            source.split('.')[0] != target.split('.')[0]
        ):
            issues.append(f'{source}:{line}: private symbol {target}.{symbol}')
            return
        if target.split('.')[0] in BUSINESS and source.split('.')[0] != (
            target.split('.')[0]
        ) and (source, target) not in EXACT_EDGES and source not in COMPOSITION:
            if symbol not in (exports.get(target) or set()):
                issues.append(f'{source}:{line}: not public in __all__: '
                              f'{target}.{symbol}')
        for origin, original_symbol in _origins(target, symbol, bindings):
            if original_symbol.startswith('_') and source.split('.')[0] != (
                origin.split('.')[0]
            ):
                issues.append(f'{source}:{line}: private re-export '
                              f'{origin}.{original_symbol}')
            if origin.split('.')[0] in BUSINESS and '.models' in origin:
                if source.split('.')[0] != origin.split('.')[0]:
                    issues.append(f'{source}:{line}: ORM re-export '
                                  f'{origin}.{original_symbol}')
            if origin in ADAPTERS and source not in COMPOSITION:
                issues.append(f'{source}:{line}: adapter re-export {origin}')

    for item in imports:
        source, target = item.source, item.target
        source_root, target_root = source.split('.')[0], target.split('.')[0]
        role = _role(source)
        if target_root in MODULES or target_root == 'main':
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
        if (source, target) in EXACT_EDGES:
            continue
        if role in {'api', 'infra'} and _role(target) == 'legacy':
            issues.append(f'{source}:{item.line}: forbidden legacy dependency {target}')
        if target in ADAPTERS or target_root == 'infrastructure':
            if source not in COMPOSITION and role != 'infra':
                issues.append(f'{source}:{item.line}: concrete adapter {target}')
        if target_root in BUSINESS and source_root != target_root:
            if source not in COMPOSITION and target.rsplit('.', 1)[-1] not in PUBLIC:
                issues.append(f'{source}:{item.line}: internal module {target}')
        if (role == 'kernel' and target_root in BUSINESS
                and target not in KERNEL_SCHEMAS):
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
                if origin.split('.')[0] in BUSINESS and '.models' in origin and (
                    source.rsplit('.', 1)[-1] != 'models'
                ):
                    issues.append(f'{source}: ORM re-export {origin}.{original_symbol}')

    for contract in _contracts(modules):
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
        role = _role(source)
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
                target_role = _role(target)
                forbidden = target_role in {
                    'composition', 'infra', 'alias', 'api', 'legacy', 'config', 'tool',
                }
                if role == 'shared':
                    forbidden = target_role != 'shared'
                if source in KERNEL_SCHEMAS and target_role == 'kernel':
                    forbidden = True
                if role == 'kernel' and target_role == 'business':
                    forbidden = target not in KERNEL_SCHEMAS and target not in {
                        'catalog', 'inventory', 'pricing',
                    }
                if forbidden:
                    issues.append(f'{source}: forbidden indirect dependency {target}')
    return sorted(set(issues))
