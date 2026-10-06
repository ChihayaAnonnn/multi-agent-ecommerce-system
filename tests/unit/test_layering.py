"""架构门禁运行真实 CLI，工具或配置缺失必须直接失败。"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CONFIG = ROOT / "pyproject.toml"
PACKAGE_ROOT = "ecom"


def write_packaged_module_stubs(root: Path) -> None:
    """按 pyproject 清单在临时包图里补齐空模块，保证契约可执行。"""
    import tomllib

    options = tomllib.loads(PACKAGED_CONFIG.read_text(encoding='utf-8'))
    inventory = options['tool']['layering']['inventory']
    parents = {module.rpartition('.')[0] for module in inventory if '.' in module}
    for module in inventory:
        # 唯一包根与含子模块的父包由目录承载，目录随子模块路径建立。
        if module in parents:
            continue
        path = root.joinpath(*module.split('.')).with_suffix('.py')
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in path.parents:
            if directory == root:
                break
            (directory / '__init__.py').touch(exist_ok=True)
        path.touch(exist_ok=True)
    allowed = root / PACKAGE_ROOT / 'kernel' / 'allowed.py'
    if allowed.exists():
        (root / PACKAGE_ROOT / 'kernel' / 'quotes.py').write_text(
            allowed.read_text(encoding='utf-8'), encoding='utf-8',
        )
        allowed.write_text('', encoding='utf-8')


def run_packaged_imports(config: Path = PACKAGED_CONFIG, root: Path = ROOT) -> str:
    if not config.is_file():
        raise FileNotFoundError(config)
    result = subprocess.run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def write_packaged_tree(tmp_path: Path, *, base_packages: tuple[str, ...] = ()) -> None:
    """搭最小 packaged 临时包图：唯一包根 + 四个基础 schemas/models + kernel 白名单。"""
    package = tmp_path / PACKAGE_ROOT
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    for name in (*('ecom.modules.catalog', 'ecom.modules.pricing',
                   'ecom.modules.inventory'), *base_packages):
        directory = tmp_path.joinpath(*name.split('.'))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / '__init__.py').write_text('', encoding='utf-8')
        for module in ('schemas', 'models'):
            (directory / f'{module}.py').write_text('', encoding='utf-8')
    kernel = package / 'kernel'
    kernel.mkdir(exist_ok=True)
    (kernel / 'allowed.py').write_text(
        'import ecom.modules.catalog.schemas\nimport ecom.modules.pricing.schemas\n'
        'import ecom.modules.inventory.schemas\n',
        encoding='utf-8',
    )
    write_packaged_module_stubs(tmp_path)


def write_packaged_config(tmp_path: Path) -> Path:
    config = tmp_path / 'pyproject.toml'
    config.write_text(PACKAGED_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    return config


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
