"""阶段架构门禁运行真实 CLI，工具或配置缺失必须直接失败。"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE_CONFIG = ROOT / ".importlinter.stage.toml"


def write_stage_module_stubs(root: Path) -> None:
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for module in options['tool']['layering']['inventory']:
        if '.' not in module or module == 'migrations.versions':
            continue
        path = root.joinpath(*module.split('.')).with_suffix('.py')
        path.parent.mkdir(parents=True, exist_ok=True)
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


def test_stage_import_contracts():
    run_stage_imports()


@pytest.mark.parametrize(('source', 'dependency'), [
    ('checkout.service', 'checkout.gateway'),
    ('commerce_orchestrator.ports', 'commerce_orchestrator.llm_adapter'),
    ('commerce_orchestrator.guards', 'commerce_orchestrator.llm_adapter'),
    ('commerce_orchestrator.service', 'commerce_orchestrator.graph'),
    ('commerce_orchestrator.service', 'commerce_orchestrator.llm_adapter'),
])
def test_composition_contract_rejects_concrete_dependencies(
    tmp_path, source, dependency,
):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in options['tool']['importlinter']['root_packages']:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ('catalog', 'pricing', 'inventory', 'cart'):
        for module in ('schemas', 'models'):
            (tmp_path / package / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
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
@pytest.mark.parametrize("dependency", ["config", "cart"])
def test_shared_contract_rejects_business_dependencies(
    tmp_path, indirect, dependency
):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding="utf-8"))
    for package in options["tool"]["importlinter"]["root_packages"]:
        directory = tmp_path / package
        directory.mkdir()
        (directory / "__init__.py").write_text("", encoding="utf-8")
    for package in ("catalog", "pricing", "inventory", "cart"):
        for module in ("schemas", "models"):
            (tmp_path / package / f"{module}.py").write_text("", encoding="utf-8")
    (tmp_path / "models" / "commerce.py").write_text("", encoding="utf-8")
    (tmp_path / "kernel" / "allowed.py").write_text(
        "import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n",
        encoding="utf-8",
    )
    shared = tmp_path / "shared"
    (shared / "__init__.py").write_text(
        "from . import helper\n" if indirect else f"import {dependency}\n",
        encoding="utf-8",
    )
    if indirect:
        (shared / "helper.py").write_text(
            f"import {dependency}\n", encoding="utf-8"
        )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / ".importlinter.stage.toml"
    config.write_text(STAGE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(AssertionError, match="BROKEN"):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('dependency', ['commerce_orchestrator', 'models.commerce'])
def test_sessions_contract_rejects_application_dependencies(
    tmp_path, indirect, dependency,
):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in options['tool']['importlinter']['root_packages']:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ('catalog', 'pricing', 'inventory', 'cart'):
        for module in ('schemas', 'models'):
            (tmp_path / package / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    sessions = tmp_path / 'shopping_session'
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    (sessions / '__init__.py').write_text(
        'from . import helper\n' if indirect else f'import {dependency}\n',
        encoding='utf-8',
    )
    if indirect:
        (sessions / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
@pytest.mark.parametrize('source', ['cart', 'storefront', 'sale_facts'])
def test_business_facts_contract_rejects_application_dependencies(
    tmp_path, indirect, source,
):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    packages = set(options['tool']['importlinter']['root_packages']) | {'sale_facts'}
    for package in packages:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ('catalog', 'pricing', 'inventory', 'cart'):
        for module in ('schemas', 'models'):
            (tmp_path / package / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    business = tmp_path / source
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    (business / '__init__.py').write_text(
        'from . import helper\n' if indirect else 'import commerce_orchestrator\n',
        encoding='utf-8',
    )
    if indirect:
        (business / 'helper.py').write_text(
            'import commerce_orchestrator\n', encoding='utf-8',
        )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_only_direct_cart_models(tmp_path, indirect):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in options['tool']['importlinter']['root_packages']:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ('catalog', 'pricing', 'inventory', 'cart'):
        for module in ('schemas', 'models'):
            (tmp_path / package / f'{module}.py').write_text('', encoding='utf-8')
    (tmp_path / 'models' / 'commerce.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    (tmp_path / 'checkout' / 'service.py').write_text(
        'import cart.service\n' if indirect else 'import cart.models\n',
        encoding='utf-8',
    )
    (tmp_path / 'cart' / 'service.py').write_text(
        'import cart.models\n', encoding='utf-8',
    )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    if indirect:
        run_stage_imports(config, tmp_path)
    else:
        with pytest.raises(AssertionError, match='BROKEN'):
            run_stage_imports(config, tmp_path)


@pytest.mark.parametrize('dependency', ['shopping_session', 'commerce_orchestrator'])
@pytest.mark.parametrize('indirect', [False, True])
def test_checkout_contract_rejects_session_or_application(
    tmp_path, dependency, indirect,
):
    import tomllib

    options = tomllib.loads(STAGE_CONFIG.read_text(encoding='utf-8'))
    for package in options['tool']['importlinter']['root_packages']:
        directory = tmp_path / package
        directory.mkdir()
        (directory / '__init__.py').write_text('', encoding='utf-8')
    for package in ('catalog', 'pricing', 'inventory'):
        (tmp_path / package / 'schemas.py').write_text('', encoding='utf-8')
    (tmp_path / 'kernel' / 'allowed.py').write_text(
        'import catalog.schemas\nimport pricing.schemas\nimport inventory.schemas\n',
        encoding='utf-8',
    )
    (tmp_path / 'checkout' / '__init__.py').write_text(
        'from . import helper\n' if indirect else f'import {dependency}\n',
        encoding='utf-8',
    )
    if indirect:
        (tmp_path / 'checkout' / 'helper.py').write_text(
            f'import {dependency}\n', encoding='utf-8',
        )
    write_stage_module_stubs(tmp_path)
    config = tmp_path / '.importlinter.stage.toml'
    config.write_text(STAGE_CONFIG.read_text(encoding='utf-8'), encoding='utf-8')
    with pytest.raises(AssertionError, match='BROKEN'):
        run_stage_imports(config, tmp_path)
