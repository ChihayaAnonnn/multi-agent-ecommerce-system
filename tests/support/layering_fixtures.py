"""分层门禁测试共用的包图与模型签名工具。

多个测试模块（layering、kernel contracts、catalog port、入口验收）共用同一份
实现，避免测试模块互相 import 形成拆分耦合。
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CONFIG = ROOT / "pyproject.toml"
PACKAGE_ROOT = "ecom"

MODEL_MODULES = (
    "ecom.modules.catalog", "ecom.modules.pricing", "ecom.modules.inventory",
    "ecom.modules.identity", "ecom.modules.sessions", "ecom.modules.cart",
    "ecom.modules.checkout", "ecom.modules.events",
)


def metadata_signature() -> dict:
    from sqlalchemy.dialects.mysql import dialect

    from ecom.shared.db import Base

    for module in MODEL_MODULES:
        importlib.import_module(f"{module}.models")

    def default_signature(default):
        if default is None:
            return None
        if hasattr(default, "sqltext"):
            return {"computed": str(default.sqltext), "persisted": default.persisted}
        value = default.arg
        return {"callable": value.__name__} if callable(value) else str(value)

    tables = {}
    for name, table in sorted(Base.metadata.tables.items()):
        constraints = []
        for constraint in table.constraints:
            entry = {
                "kind": type(constraint).__name__, "name": constraint.name,
                "columns": [column.name for column in constraint.columns],
            }
            if hasattr(constraint, "elements"):
                entry["references"] = [
                    element.target_fullname for element in constraint.elements
                ]
                entry["ondelete"] = constraint.ondelete
                entry["onupdate"] = constraint.onupdate
            if hasattr(constraint, "sqltext"):
                entry["sqltext"] = str(constraint.sqltext)
            constraints.append(entry)
        tables[name] = {
            "columns": [{
                "name": column.name, "type": column.type.compile(dialect=dialect()),
                "nullable": column.nullable, "primary_key": column.primary_key,
                "autoincrement": column.autoincrement,
                "default": default_signature(column.default),
                "server_default": default_signature(column.server_default),
                "onupdate": default_signature(column.onupdate),
                "server_onupdate": default_signature(column.server_onupdate),
            } for column in table.columns],
            "constraints": sorted(constraints, key=lambda value: json.dumps(value)),
            "indexes": sorted([{
                "name": index.name, "unique": index.unique,
                "expressions": [str(expression) for expression in index.expressions],
            } for index in table.indexes], key=lambda value: value["name"]),
            "options": dict(table.dialect_kwargs),
        }
    return {"modules": list(MODEL_MODULES), "tables": tables}


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


def run_packaged_imports(
    config: Path = PACKAGED_CONFIG, root: Path = ROOT
) -> str:
    if not config.is_file():
        raise FileNotFoundError(config)
    result = subprocess.run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def write_packaged_tree(
    tmp_path: Path, *, base_packages: tuple[str, ...] = ()
) -> None:
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
