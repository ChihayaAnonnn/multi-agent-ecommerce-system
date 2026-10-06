"""以隔离配置捕获旧应用合同，禁止连接开发服务或调用真实模型。"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.layering_reports import CONTRACT_FILES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "layering"
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


async def capture_application_contracts(directory: Path) -> None:
    for variable in list(os.environ):
        if variable.startswith(("ECOM_", "OPENAI_")):
            del os.environ[variable]

    import ecom.config as settings_module

    isolated_settings = settings_module.Settings(
        _env_file=None, database_url="", llm_api_key="test-only-not-a-secret",
        identity_environment="production", commerce_demo_enabled=False,
    )
    # 组合根在 create_app() 调用时取配置；先打桩再导入即可隔离环境。
    settings_module.get_settings = lambda: isolated_settings
    from httpx import ASGITransport, AsyncClient

    from ecom.main import create_app

    app = create_app()

    def effective_routes(routes):
        for route in routes:
            if hasattr(route, "effective_candidates"):
                yield from effective_routes(route.effective_candidates())
            else:
                yield route

    contracts = {
        "routes.json": [{
            "path": route.path, "name": route.name,
            "methods": sorted(getattr(route, "methods", None) or []),
            "include_in_schema": getattr(route, "include_in_schema", False),
        } for route in effective_routes(app.routes)],
        "openapi.json": app.openapi(),
        "metadata.json": metadata_signature(),
    }
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = []
            for method, path, body, headers in (
                ("GET", "/health", None, {}),
                ("GET", "/api/v1/catalog/products", None, {}),
                ("GET", "/api/v1/catalog/products/missing", None, {}),
                ("GET", "/api/v1/catalog/skus/missing", None, {}),
                ("GET", "/api/v1/commerce/skus/missing/quote", None, {}),
                ("POST", "/api/v1/auth/login",
                 {"email": "synthetic@example.test", "password": "synthetic-password"},
                 {}),
                ("POST", "/api/v1/commerce/sessions", {},
                 {"Authorization": "Bearer synthetic-token"}),
            ):
                response = await client.request(
                    method, path, json=body, headers=headers
                )
                responses.append({
                    "method": method, "path": path,
                    "status_code": response.status_code, "body": response.json(),
                    "headers": dict(response.headers),
                })
            contracts["errors.json"] = responses
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in contracts.items():
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@pytest.fixture(scope="session")
def application_contracts(tmp_path_factory, pytestconfig) -> dict:
    directory = tmp_path_factory.mktemp("layering-contracts")
    target = pytestconfig.getoption("--layering-artifacts")
    if target:
        directory = Path(target).resolve()
    environment = {
        key: value for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "LANG", "LC_ALL"}
    }
    environment["ECOM_LLM_API_KEY"] = "test-only-not-a-secret"
    subprocess.run(
        [sys.executable, "-m", "tests.test_layering_baseline",
         "--capture", str(directory)],
        cwd=ROOT, env=environment, check=True, capture_output=True, text=True,
    )
    return {
        name: json.loads((directory / name).read_text(encoding="utf-8"))
        for name in CONTRACT_FILES
    }


def assert_contract(name: str, application_contracts: dict) -> None:
    assert application_contracts[name] == json.loads(
        (FIXTURES / name).read_text(encoding="utf-8")
    )


def test_routes_and_openapi_match_baseline(application_contracts):
    assert_contract("routes.json", application_contracts)
    assert_contract("openapi.json", application_contracts)


def test_metadata_signature_matches_baseline(application_contracts):
    assert_contract("metadata.json", application_contracts)


def test_missing_configuration_responses_match_baseline(application_contracts):
    assert_contract("errors.json", application_contracts)


def test_desktop_demo_contract_runner():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "run_demo_contracts.cjs")],
        cwd=ROOT.parent, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "10 desktop chat contracts passed"


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--capture":
        raise SystemExit("usage: --capture DIRECTORY")
    asyncio.run(capture_application_contracts(Path(sys.argv[2])))
