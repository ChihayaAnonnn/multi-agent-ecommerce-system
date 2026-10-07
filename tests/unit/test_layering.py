"""架构门禁运行真实 CLI，工具或配置缺失必须直接失败。"""

import pytest

from tests.support.layering_fixtures import (
    PACKAGE_ROOT,
    run_packaged_imports,
    write_packaged_config,
    write_packaged_tree,
)


def test_packaged_import_contracts():
    run_packaged_imports()


@pytest.mark.parametrize(('source', 'dependency'), [
    ('ecom.modules.checkout.service', 'ecom.infra.checkout_gateway'),
    ('ecom.application.shopping.ports', 'ecom.infra.llm'),
    ('ecom.application.shopping.guards', 'ecom.infra.llm'),
    ('ecom.application.shopping.orchestrator', 'ecom.application.shopping.graph'),
    ('ecom.application.shopping.orchestrator', 'ecom.infra.llm'),
])
def test_composition_contract_rejects_concrete_dependencies(
    tmp_path, source, dependency,
):
    write_packaged_tree(tmp_path, base_packages=('ecom.modules.cart',))
    config = write_packaged_config(tmp_path)
    run_packaged_imports(config, tmp_path)
    tmp_path.joinpath(*source.split('.')).with_suffix('.py').write_text(
        f'import {dependency}\n', encoding='utf-8',
    )
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


def test_packaged_wrapper_rejects_missing_configuration(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_packaged_imports(tmp_path / "missing.toml")


def test_packaged_wrapper_rejects_missing_cli(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError):
        run_packaged_imports()


@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("dependency", ["ecom.config", "ecom.modules.cart"])
def test_shared_contract_rejects_business_dependencies(
    tmp_path, indirect, dependency
):
    write_packaged_tree(tmp_path, base_packages=('ecom.modules.cart',))
    shared = tmp_path / PACKAGE_ROOT / "shared"
    (shared / "__init__.py").write_text(
        "from . import helper\n" if indirect else f"import {dependency}\n",
        encoding="utf-8",
    )
    if indirect:
        (shared / "helper.py").write_text(
            f"import {dependency}\n", encoding="utf-8"
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match="BROKEN"):
        run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('dependency', [
    'ecom.application', 'ecom.application.shopping.contracts',
])
def test_sessions_contract_rejects_application_dependencies(
    tmp_path, indirect, dependency,
):
    write_packaged_tree(tmp_path)

    def write_sessions(source: str) -> None:
        (tmp_path / PACKAGE_ROOT / 'modules' / 'sessions' / '__init__.py').write_text(
            source, encoding='utf-8',
        )

    write_sessions(
        'from . import helper\n' if indirect else f'import {dependency}\n'
    )
    if indirect:
        (tmp_path / PACKAGE_ROOT / 'modules' / 'sessions' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('source', ['ecom.modules.cart', 'ecom.application.storefront',
                                    'ecom.modules.sale_facts'])
def test_business_facts_contract_rejects_application_dependencies(
    tmp_path, indirect, source,
):
    write_packaged_tree(tmp_path, base_packages=('ecom.modules.cart',))
    business = tmp_path.joinpath(*source.split('.'))
    business.mkdir(exist_ok=True)
    (business / '__init__.py').write_text(
        'from . import helper\n' if indirect else 'import ecom.application.shopping\n',
        encoding='utf-8',
    )
    if indirect:
        (business / 'helper.py').write_text(
            'import ecom.application.shopping\n', encoding='utf-8',
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('source', ['ecom.modules.inventory', 'ecom.modules.events'])
def test_modules_contract_rejects_application_dependencies(tmp_path, indirect, source):
    """通用方向：任何业务模块都不得依赖应用层，不能只靠 cart/checkout 专项规则。"""
    write_packaged_tree(tmp_path)
    package = tmp_path.joinpath(*source.split('.'))
    (package / '__init__.py').write_text(
        'from . import helper\n' if indirect else 'import ecom.application.shopping\n',
        encoding='utf-8',
    )
    if indirect:
        (package / 'helper.py').write_text(
            'import ecom.application.shopping\n', encoding='utf-8',
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
def test_infra_contract_rejects_api_dependencies(tmp_path, indirect):
    """适配器不得反向依赖 HTTP 层。"""
    write_packaged_tree(tmp_path)
    infra = tmp_path / PACKAGE_ROOT / 'infra'
    (infra / 'llm.py').write_text(
        'from . import helper\n' if indirect else 'import ecom.api.errors\n',
        encoding='utf-8',
    )
    if indirect:
        (infra / 'helper.py').write_text(
            'import ecom.api.errors\n', encoding='utf-8',
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


def test_infra_contract_allows_config_and_kernel(tmp_path):
    """反向约束不能误伤：适配器依赖 config/kernel 是设计允许的。"""
    write_packaged_tree(tmp_path)
    (tmp_path / PACKAGE_ROOT / 'infra' / 'llm.py').write_text(
        'import ecom.config\nimport ecom.kernel.quotes\n', encoding='utf-8',
    )
    config = write_packaged_config(tmp_path)
    run_packaged_imports(config, tmp_path)


def test_adapter_protection_covers_infra_package_root(tmp_path):
    """保护整个 `ecom.infra` 前缀：包级入口也不能被非组合根导入。

    这里刻意导入定义在 `ecom/infra/__init__.py` 的名字（而非子模块），
    否则 import-linter 会把它解析成受保护的叶子模块，测不到包根缺口。
    """
    write_packaged_tree(tmp_path)
    (tmp_path / PACKAGE_ROOT / 'infra' / '__init__.py').write_text(
        'class Client: pass\n', encoding='utf-8',
    )
    (tmp_path / PACKAGE_ROOT / 'modules' / 'catalog' / 'service.py').write_text(
        'from ecom.infra import Client\n', encoding='utf-8',
    )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


def test_adapter_protection_allows_composition_root_package_import(tmp_path):
    write_packaged_tree(tmp_path)
    (tmp_path / PACKAGE_ROOT / 'api' / 'deps.py').write_text(
        'from ecom.infra import db\n', encoding='utf-8',
    )
    config = write_packaged_config(tmp_path)
    run_packaged_imports(config, tmp_path)


def test_adapter_protection_covers_legacy_infrastructure_prefix(tmp_path):
    """`ecom.legacy.infrastructure` 是深层前缀，契约必须整段保护其子树。

    CLI 只以 `ecom` 为 root，不分析 `tools` 来源；来自 tools 的同类越界由
    AST 检查器负责（见 test_layering_rules 的前缀反例）。
    """
    write_packaged_tree(tmp_path)
    (tmp_path / PACKAGE_ROOT / 'legacy' / 'agents' / 'base_agent.py').write_text(
        'from ecom.legacy.infrastructure import mysql_client\n', encoding='utf-8',
    )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_only_direct_cart_models(tmp_path, indirect):
    write_packaged_tree(tmp_path, base_packages=('ecom.modules.cart',))
    (tmp_path / PACKAGE_ROOT / 'modules' / 'checkout' / 'service.py').write_text(
        'import ecom.modules.cart.service\n' if indirect
        else 'import ecom.modules.cart.models\n',
        encoding='utf-8',
    )
    (tmp_path / PACKAGE_ROOT / 'modules' / 'cart' / 'service.py').write_text(
        'import ecom.modules.cart.models\n', encoding='utf-8',
    )
    config = write_packaged_config(tmp_path)
    if indirect:
        run_packaged_imports(config, tmp_path)
    else:
        with pytest.raises(AssertionError, match='BROKEN'):
            run_packaged_imports(config, tmp_path)


@pytest.mark.parametrize('dependency', ['ecom.modules.sessions', 'ecom.application'])
@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_session_or_application(
    tmp_path, dependency, indirect,
):
    write_packaged_tree(tmp_path)
    (tmp_path / PACKAGE_ROOT / 'modules' / 'checkout' / '__init__.py').write_text(
        'from . import helper\n' if indirect else f'import {dependency}\n',
        encoding='utf-8',
    )
    if indirect:
        (tmp_path / PACKAGE_ROOT / 'modules' / 'checkout' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    config = write_packaged_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_packaged_imports(config, tmp_path)
