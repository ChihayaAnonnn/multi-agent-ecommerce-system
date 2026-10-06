"""使用本地源文件图验证架构边界，不导入业务基础设施。"""

import importlib
import subprocess
from pathlib import Path

import pytest


def rules():
    try:
        return importlib.import_module('tests.layering_rules')
    except ModuleNotFoundError:
        pytest.fail('架构检查器 tests.layering_rules 尚未实现')


def write_module(root, name, source=''):
    path = root.joinpath(*name.split('.')).with_suffix('.py')
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in path.parents:
        if directory == root:
            break
        (directory / '__init__.py').touch()
    path.write_text(source, encoding='utf-8')


@pytest.fixture
def layout_case(tmp_path):
    for name, source in {
        'modules.catalog.models': 'class Product: pass\n',
        'modules.catalog.schemas':
            '__all__ = ["ProductView"]\nclass ProductView: pass\n',
        'modules.catalog.service': (
            'from modules.catalog.models import Product\n'
            '__all__ = ["CatalogService"]\nclass CatalogService: pass\n'
        ),
        'modules.inventory.service':
            'from modules.catalog.service import CatalogService\n',
        'kernel.quotes': 'from modules.catalog.schemas import ProductView\n',
        'shared.time': '',
        'api.deps': 'import infra.checkout_gateway as gateway\n',
        'api.routes.catalog': '',
        'infra.checkout_gateway': '',
        'application.shopping.orchestrator': '',
        'legacy.agents.base_agent': '',
        'config.settings': '',
    }.items():
        write_module(tmp_path, name, source)
    return tmp_path


def test_indirect_model_use_is_allowed_but_direct_use_is_not(layout_case):
    checker = rules().check_import_boundaries
    assert checker(layout_case, 'grouped') == []
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.models\n')
    assert any('modules.catalog.models' in item
               for item in checker(layout_case, 'grouped'))


@pytest.mark.parametrize(('source', 'statement', 'target'), [
    ('modules.inventory.service',
     'from modules.catalog.service import Product', 'Product'),
    ('modules.inventory.service',
     'from modules.catalog.service import _private', '_private'),
    ('modules.inventory.service',
     'import modules.catalog.service as service\nservice.Product', 'Product'),
    ('modules.inventory.service', 'from modules.catalog import Product',
     'modules.catalog'),
    ('modules.inventory.service', 'from modules.catalog.service import *', '*'),
    ('kernel.quotes', 'import modules.catalog.service', 'modules.catalog.service'),
    ('application.shopping.orchestrator', 'import api.routes.catalog',
     'api.routes.catalog'),
    ('api.routes.catalog', 'import infra.checkout_gateway',
     'infra.checkout_gateway'),
    ('modules.inventory.service', 'from modules.catalog import models',
     'modules.catalog.models'),
    ('api.routes.catalog', 'from .. import deps\nfrom ..deps import gateway',
     'gateway'),
    ('api.routes.catalog', 'from .. import deps\ndeps.gateway',
     'infra.checkout_gateway'),
    ('application.shopping.orchestrator', 'from ....api import routes', 'relative'),
    ('modules.inventory.service',
     'def run():\n    import modules.catalog.models', 'modules.catalog.models'),
    ('modules.inventory.service',
     'from typing import TYPE_CHECKING\n'
     'if TYPE_CHECKING:\n    import modules.catalog.models',
     'modules.catalog.models'),
    ('modules.inventory.service',
     '__import__("modules.catalog.models")', 'dynamic'),
    ('modules.inventory.service', 'import importlib as loader\n'
     'loader.import_module("modules.catalog.models")', 'dynamic'),
    ('modules.inventory.service', 'from importlib import import_module as load\n'
     'load("modules.catalog.models")', 'dynamic'),
    ('modules.inventory.service', 'loader = __import__\n'
     'loader("modules.catalog.models")', 'dynamic'),
    ('modules.inventory.service', 'import fastapi', 'fastapi'),
    ('kernel.quotes', 'import sqlalchemy', 'sqlalchemy'),
    ('shared.time', 'import config', 'config'),
    ('modules.inventory.service', 'import legacy', 'legacy'),
    ('api.routes.catalog', 'import legacy', 'legacy'),
    ('modules.inventory.service', 'import modules.catalog.service as service\n'
     'getattr(service, "_private")', 'dynamic'),
    ('kernel.quotes', 'open("anything")', 'I/O'),
])
def test_rejects_import_bypasses(layout_case, source, statement, target):
    write_module(layout_case, source, statement + '\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any(target in issue for issue in issues), issues


@pytest.mark.parametrize('statement', [
    'from modules.catalog.models import Product\n',
    'from modules.catalog.models import Product as PublicProduct\n',
    'import modules.catalog.models as models\nPublicProduct = models.Product\n',
])
def test_public_all_cannot_reexport_orm(layout_case, statement):
    public = 'PublicProduct' if 'PublicProduct' in statement else 'Product'
    write_module(layout_case, 'modules.catalog.service',
                 statement + f'__all__ = [{public!r}]\n')
    write_module(layout_case, 'modules.inventory.service',
                 f'from modules.catalog.service import {public}\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('ORM' in issue for issue in issues), issues


@pytest.mark.parametrize('statement', [
    'from modules.catalog.models import Product\nPublicProduct: type = Product\n',
    'import modules.catalog.models as models\nmodel_alias: object = models\n'
    'PublicProduct: type = model_alias.Product\n',
])
def test_annotated_public_alias_cannot_reexport_orm(layout_case, statement):
    write_module(layout_case, 'modules.catalog.service',
                 statement + '__all__ = ["PublicProduct"]\n')
    write_module(layout_case, 'modules.inventory.service',
                 'from modules.catalog.service import PublicProduct\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('ORM re-export modules.catalog.models.Product' in issue
               for issue in issues), issues


@pytest.mark.parametrize('assignment', [
    'alias = service\n',
    'alias: object = service\n',
    'first: object = service\nalias = first\n',
])
@pytest.mark.parametrize(('access', 'expected'), [
    ('alias._private()\n', 'private symbol modules.catalog.service._private'),
    ('getattr(alias, "_private")\n', 'dynamic module access'),
])
def test_module_alias_preserves_private_and_reflection_boundaries(
    layout_case, assignment, access, expected,
):
    write_module(layout_case, 'modules.catalog.service',
                 '__all__ = []\ndef _private(): pass\n')
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.service as service\n' + assignment + access)
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any(expected in issue for issue in issues), issues


@pytest.mark.parametrize('annotation', ['', ': type'])
def test_public_dto_value_alias_remains_legal(layout_case, annotation):
    write_module(layout_case, 'modules.catalog.service',
                 'from modules.catalog.schemas import ProductView\n'
                 f'PublicView{annotation} = ProductView\n'
                 '__all__ = ["PublicView"]\n')
    write_module(layout_case, 'modules.inventory.service',
                 'from modules.catalog.service import PublicView\n')
    assert rules().check_import_boundaries(layout_case, 'grouped') == []


@pytest.mark.parametrize('assignment', [
    'alias = service\n',
    'alias: object = service\n',
    'first: object = service\nalias = first\n',
])
def test_module_alias_allows_public_service_access(layout_case, assignment):
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.service as service\n' + assignment
                 + 'alias.CatalogService()\n')
    assert rules().check_import_boundaries(layout_case, 'grouped') == []


@pytest.mark.parametrize('annotation', ['', ': type'])
@pytest.mark.parametrize(('access', 'expected'), [
    ('service._private()\n', 'private symbol modules.catalog.service._private'),
    ('getattr(service, "_private")\n', 'dynamic module access'),
])
def test_later_assignment_cannot_erase_earlier_module_access(
    layout_case, annotation, access, expected,
):
    write_module(layout_case, 'modules.catalog.service',
                 '__all__ = ["CatalogService"]\nclass CatalogService: pass\n'
                 'def _private(): pass\n')
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.service as service\n' + access
                 + f'service{annotation} = service.CatalogService\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any(expected in issue for issue in issues), issues


@pytest.mark.parametrize('annotation', ['', ': type'])
@pytest.mark.parametrize(('access', 'expected'), [
    ('alias._private()', 'private symbol modules.catalog.service._private'),
    ('getattr(alias, "_private")', 'dynamic module access'),
])
def test_function_alias_history_survives_later_assignment(
    layout_case, annotation, access, expected,
):
    write_module(layout_case, 'modules.inventory.service',
                 'def run():\n    import modules.catalog.service as service\n'
                 '    first: object = service\n    alias = first\n'
                 f'    {access}\n'
                 f'    alias{annotation} = service.CatalogService\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any(expected in issue for issue in issues), issues


@pytest.mark.parametrize('annotation', ['', ': type'])
def test_public_access_then_public_rebinding_is_legal(layout_case, annotation):
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.service as service\n'
                 'alias: object = service\nalias.CatalogService()\n'
                 f'alias{annotation} = service.CatalogService\nalias()\n')
    assert rules().check_import_boundaries(layout_case, 'grouped') == []


@pytest.mark.parametrize(('setup', 'callable_name'), [
    ('', '{function}'),
    ('read_member = {function}\n', 'read_member'),
    ('read_member: object = {function}\n', 'read_member'),
    ('first: object = {function}\nread_member = first\n', 'read_member'),
    ('from builtins import {function} as read_member\n', 'read_member'),
    ('import builtins as builtin\n', 'builtin.{function}'),
    ('import builtins as builtin\nmodule_alias: object = builtin\n'
     'first = module_alias.{function}\nread_member: object = first\n',
     'read_member'),
])
@pytest.mark.parametrize(('function', 'arguments'), [
    ('getattr', '{target}, "_private", None'),
    ('setattr', '{target}, "_private", None'),
    ('vars', '{target}'),
])
@pytest.mark.parametrize('project_module', [True, False])
def test_reflection_callable_identity_preserves_module_boundary(
    layout_case, setup, callable_name, function, arguments, project_module,
):
    target = 'service' if project_module else 'ordinary'
    source = (
        'import modules.catalog.service as service\n'
        'class Ordinary: pass\nordinary = Ordinary()\n'
        + setup.format(function=function)
        + callable_name.format(function=function)
        + '(' + arguments.format(target=target) + ')\n'
    )
    write_module(layout_case, 'modules.inventory.service', source)
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    if project_module:
        assert any('dynamic module access' in issue for issue in issues), issues
    else:
        assert issues == []


@pytest.mark.parametrize(('function', 'arguments'), [
    ('getattr', 'service, "_private", None'),
    ('setattr', 'service, "_private", None'),
    ('vars', 'service'),
])
def test_reflection_callable_history_survives_later_assignment(
    layout_case, function, arguments,
):
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.service as service\n'
                 f'first: object = {function}\nread_member = first\n'
                 f'read_member({arguments})\n'
                 'read_member: object = service.CatalogService\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('dynamic module access' in issue for issue in issues), issues


def test_package_reexport_cannot_hide_orm(layout_case):
    (layout_case / 'modules/catalog/__init__.py').write_text(
        'from .models import Product\n__all__ = ["Product"]\n', encoding='utf-8',
    )
    write_module(layout_case, 'modules.inventory.service',
                 'from modules.catalog import Product\n')
    assert rules().check_import_boundaries(layout_case, 'grouped')


def test_upper_forbidden_checks_indirect_dependencies(layout_case):
    write_module(layout_case, 'modules.catalog.service',
                 'import api.routes.catalog\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('modules.inventory.service' in issue and 'api.routes.catalog' in issue
               for issue in issues), issues


@pytest.mark.parametrize('source', [
    'from importlib import import_module as load\n',
    'import importlib\ngetattr(importlib, "import_module")("modules.catalog.models")\n',
    'from builtins import __import__ as load\nload("modules.catalog.models")\n',
])
def test_dynamic_aliases_cannot_hide_imports(layout_case, source):
    write_module(layout_case, 'modules.inventory.service', source)
    assert any('dynamic' in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))


def test_public_alias_cannot_hide_private_symbol(layout_case):
    write_module(layout_case, 'modules.catalog.schemas',
                 '__all__ = []\nclass _Private: pass\n')
    write_module(layout_case, 'modules.catalog.service',
                 'from modules.catalog.schemas import _Private as Public\n'
                 '__all__ = ["Public"]\n')
    write_module(layout_case, 'modules.inventory.service',
                 'from modules.catalog.service import Public\n')
    assert any('private' in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))


def test_package_initializer_cannot_hide_indirect_adapter(layout_case):
    write_module(layout_case, 'main', 'import infra.checkout_gateway\n')
    (layout_case / 'modules/catalog/__init__.py').write_text('import main\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('modules.catalog' in issue and 'infra.checkout_gateway' in issue
               for issue in issues), issues


def test_legal_kernel_schema_cannot_hide_upper_dependency(layout_case):
    write_module(layout_case, 'modules.catalog.schemas',
                 'import api.routes.catalog\n__all__ = ["ProductView"]\n'
                 'class ProductView: pass\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('kernel.quotes' in issue and 'api.routes.catalog' in issue
               for issue in issues), issues


def test_import_runs_parent_initializer_dependencies(layout_case):
    (layout_case / 'modules/catalog/__init__.py').write_text(
        'import api.routes.catalog\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('modules.inventory.service' in issue and 'api.routes.catalog' in issue
               for issue in issues), issues


def test_base_schema_parent_cannot_hide_kernel_dependency(layout_case):
    (layout_case / 'modules/catalog/__init__.py').write_text('import kernel.quotes\n')
    issues = rules().check_import_boundaries(layout_case, 'grouped')
    assert any('modules.catalog.schemas' in issue and 'kernel.quotes' in issue
               for issue in issues), issues


@pytest.mark.parametrize('name', [
    'new_business.service', 'modules.catalog.unregistered',
])
def test_new_unregistered_modules_fail_checker_and_generator(layout_case, name):
    write_module(layout_case, name)
    assert any(name in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))
    with pytest.raises(ValueError, match='unregistered'):
        rules().generate_import_contracts(layout_case, 'grouped')


def test_missing_project_target_fails(layout_case):
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.pricing.service\n')
    assert any('missing' in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))
    with pytest.raises(ValueError, match='missing'):
        rules().generate_import_contracts(layout_case, 'grouped')


def test_complete_project_inventory_rejects_missing_roots(layout_case):
    (layout_case / 'pyproject.toml').write_text(
        '[tool.layering]\nlayout = "grouped"\nrequire_complete_inventory = true\n',
        encoding='utf-8',
    )
    assert any('missing registered module: modules.pricing' in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))
    with pytest.raises(ValueError, match='missing registered'):
        rules().generate_import_contracts(layout_case, 'grouped')


def test_generated_inventory_includes_namespace_parents(layout_case):
    import tomllib

    path = layout_case / 'migrations/versions/0001_catalog.py'
    path.parent.mkdir(parents=True)
    path.touch()
    config = tomllib.loads(rules().generate_import_contracts(layout_case, 'grouped'))
    inventory = config['tool']['layering']['inventory']
    assert inventory['migrations'] == 'tool'
    assert inventory['migrations.versions'] == 'tool'
    assert inventory['migrations.versions.0001_catalog'] == 'tool'


@pytest.mark.parametrize('source', ['api.routes.catalog', 'modules.inventory.service'])
def test_precise_adapter_exception_does_not_allow_other_consumers(
    layout_case, source,
):
    write_module(layout_case, 'infra.db')
    write_module(layout_case, source, 'from infra.db import create_database\n')
    assert any('infra.db' in issue for issue in
               rules().check_import_boundaries(layout_case, 'grouped'))


def test_composition_root_can_import_registered_adapter(layout_case):
    write_module(layout_case, 'api.deps', 'import infra.checkout_gateway\n')
    assert rules().check_import_boundaries(layout_case, 'grouped') == []


def test_generated_contracts_execute_real_cli(layout_case):
    config = layout_case / '.importlinter.toml'
    config.write_text(rules().generate_import_contracts(layout_case, 'grouped'),
                      encoding='utf-8')
    command = ['lint-imports', '--config', str(config), '--no-cache']
    result = subprocess.run(command, cwd=layout_case, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    write_module(layout_case, 'modules.inventory.service',
                 'import modules.catalog.models\n')
    result = subprocess.run(command, cwd=layout_case, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'BROKEN' in result.stdout


def test_original_repository_passes_ast_and_has_current_generated_contracts():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    options = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    layout = options['tool']['layering']['layout']
    assert rules().check_import_boundaries(root, layout) == []
    assert (root / '.importlinter.stage.toml').read_text(encoding='utf-8') == (
        rules().generate_import_contracts(root, layout)
    )


@pytest.mark.parametrize('layout', ['packaged', 'unknown', ''])
def test_absent_or_unknown_layout_fails_closed(layout_case, layout):
    with pytest.raises(ValueError):
        rules().generate_import_contracts(layout_case, layout)
    with pytest.raises(ValueError):
        rules().check_import_boundaries(layout_case, layout)


def test_original_layout_remains_reproducible(tmp_path):
    """历史证据：`original` 平铺布局仍可复现检查结果与契约生成。"""
    for name, source in {
        'catalog.models': 'class Product: pass\n',
        'catalog.schemas': '__all__ = ["ProductView"]\nclass ProductView: pass\n',
        'catalog.service':
            '__all__ = ["CatalogService"]\nclass CatalogService: pass\n',
        'inventory.service': 'from catalog.service import CatalogService\n',
        'kernel.quotes': 'from catalog.schemas import ProductView\n',
        'shared.time': '',
        'api.runtime': 'import checkout.gateway as gateway\n',
        'api.catalog': '',
        'checkout.gateway': '',
        'commerce_orchestrator.service': '',
        'agents.base_agent': '',
        'config.settings': '',
    }.items():
        write_module(tmp_path, name, source)
    checker = rules().check_import_boundaries
    assert checker(tmp_path, 'original') == []
    assert rules().generate_import_contracts(tmp_path, 'original')
    write_module(tmp_path, 'inventory.service', 'import catalog.models\n')
    assert any('catalog.models' in item for item in checker(tmp_path, 'original'))
