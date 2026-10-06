"""阶段架构门禁运行真实 CLI，工具或配置缺失必须直接失败。"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE_CONFIG = ROOT / ".importlinter.stage.toml"


def write_stage_module_stubs(root: Path) -> None:
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    inventory = options['tool']['layering']['inventory']
    parents = {module.rpartition('.')[0] for module in inventory if '.' in module}
    for module in inventory:
        # 顶层目录由 root_packages 循环建好；父包目录下只有子模块文件。
        if '.' not in module or module in parents:
            continue
        path = root.joinpath(*module.split('.')).with_suffix('.py')
        path.parent.mkdir(parents=True, exist_ok=True)
        for directory in path.parents:
            if directory == root:
                break
            (directory / '__init__.py').touch(exist_ok=True)
        path.touch(exist_ok=True)
    allowed = root / 'kernel' / 'allowed.py'
    if allowed.exists():
        (root / 'kernel' / 'quotes.py').write_text(
            allowed.read_text(encoding='utf-8'), encoding='utf-8',
        )
        allowed.write_text('', encoding='utf-8')


def run_stage_imports(config: Path = STAGE_CONFIG, root: Path = ROOT) -> str:
    if not config.is_file():
        raise FileNotFoundError(config)
    result = subprocess.run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def write_grouped_tree(tmp_path: Path, *, base_packages: tuple[str, ...] = ()) -> None:
    """搭最小 grouped 临时包图：根包 + 四个基础 schemas/models + kernel 白名单。"""
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in options['tool']['importlinter']['root_packages']:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in (*('modules.catalog', 'modules.pricing', 'modules.inventory'),
                    *base_packages):
        directory = tmp_path.joinpath(*package.split('.'))
        directory.mkdir(exist_ok=True)
        (directory / '__init__.py').write_text('', encoding='utf-8')
        for module in ('schemas', 'models'):
            (directory / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import modules.catalog.schemas\nimport modules.pricing.schemas\n'
        'import modules.inventory.schemas\n',
        encoding='utf-8',
    )
    write_stage_module_stubs(tmp_path)


def write_stage_config(tmp_path: Path) -> Path:
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    return config


def test_stage_import_contracts():
    run_stage_imports()


@pytest.mark.parametrize(('source', 'dependency'), [
    ('modules.checkout.service', 'infra.checkout_gateway'),
    ('application.shopping.ports', 'infra.llm'),
    ('application.shopping.guards', 'infra.llm'),
    ('application.shopping.orchestrator', 'application.shopping.graph'),
    ('application.shopping.orchestrator', 'infra.llm'),
])
def test_composition_contract_rejects_concrete_dependencies(
    tmp_path, source, dependency,
):
    write_grouped_tree(tmp_path, base_packages=('modules.cart',))
    config = write_stage_config(tmp_path)
    run_stage_imports(config, tmp_path)
    tmp_path.joinpath(*source.split('.')).with_suffix('.py').write_text(
        f'import {dependency}\n', encoding='utf-8',
    )
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


def test_stage_wrapper_rejects_missing_configuration(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_stage_imports(tmp_path / "missing.toml")


def test_stage_wrapper_rejects_missing_cli(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError):
        run_stage_imports()


@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("dependency", ["config", "modules.cart"])
def test_shared_contract_rejects_business_dependencies(
    tmp_path, indirect, dependency
):
    write_grouped_tree(tmp_path, base_packages=('modules.cart',))
    shared = tmp_path / "shared"
    (shared / "__init__.py").write_text(
        "from . import helper\n" if indirect else f"import {dependency}\n",
        encoding="utf-8",
    )
    if indirect:
        (shared / "helper.py").write_text(
            f"import {dependency}\n", encoding="utf-8"
        )
    config = write_stage_config(tmp_path)
    with pytest.raises(AssertionError, match="BROKEN"):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('dependency', [
    'application', 'application.shopping.contracts',
])
def test_sessions_contract_rejects_application_dependencies(
    tmp_path, indirect, dependency,
):
    write_grouped_tree(tmp_path)

    def write_sessions(source: str) -> None:
        (tmp_path / 'modules' / 'sessions' / '__init__.py').write_text(
            source, encoding='utf-8',
        )

    write_sessions(
        'from . import helper\n' if indirect else f'import {dependency}\n'
    )
    if indirect:
        (tmp_path / 'modules' / 'sessions' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    config = write_stage_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('source', ['modules.cart', 'application.storefront',
                                    'modules.sale_facts'])
def test_business_facts_contract_rejects_application_dependencies(
    tmp_path, indirect, source,
):
    write_grouped_tree(tmp_path, base_packages=('modules.cart',))
    business = tmp_path.joinpath(*source.split('.'))
    business.mkdir(exist_ok=True)
    (business / '__init__.py').write_text(
        'from . import helper\n' if indirect else 'import application.shopping\n',
        encoding='utf-8',
    )
    if indirect:
        (business / 'helper.py').write_text(
            'import application.shopping\n', encoding='utf-8',
        )
    config = write_stage_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_only_direct_cart_models(tmp_path, indirect):
    write_grouped_tree(tmp_path, base_packages=('modules.cart',))
    (tmp_path / 'modules' / 'checkout' / 'service.py').write_text(
        'import modules.cart.service\n' if indirect else 'import modules.cart.models\n',
        encoding='utf-8',
    )
    (tmp_path / 'modules' / 'cart' / 'service.py').write_text(
        'import modules.cart.models\n', encoding='utf-8',
    )
    config = write_stage_config(tmp_path)
    if indirect:
        run_stage_imports(config, tmp_path)
    else:
        with pytest.raises(AssertionError, match='BROKEN'):
            run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('dependency', ['modules.sessions', 'application'])
@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_session_or_application(
    tmp_path, dependency, indirect,
):
    write_grouped_tree(tmp_path)
    (tmp_path / 'modules' / 'checkout' / '__init__.py').write_text(
        'from . import helper\n' if indirect else f'import {dependency}\n',
        encoding='utf-8',
    )
    if indirect:
        (tmp_path / 'modules' / 'checkout' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    config = write_stage_config(tmp_path)
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)
