"""唯一包根入口（Task 15）验收：工厂、lifespan、演示路径与冻结元数据。

全部用例用显式 `Settings`、本地桩与 monkeypatch 隔离环境：
不读 `.env`、不连 Redis/MySQL、不构造任何真实模型客户端。
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ecom.config import Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "layering"


def isolated_settings() -> Settings:
    """与基线捕获一致的隔离配置：不读环境、不启用演示、不触发外部服务。"""
    return Settings(
        _env_file=None, database_url="", llm_api_key="test-only-not-a-secret",
        identity_environment="production", commerce_demo_enabled=False,
    )


def effective_routes(routes):
    """展开演示路由等动态容器，得到冻结契约同样粒度的路由序列。"""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from effective_routes(route.effective_candidates())
        else:
            yield route


def test_factory_routes_match_original_contract():
    """`create_app()` 的注册结果必须与搬迁前捕获的冻结路由契约逐字一致。"""
    from ecom.main import create_app

    app = create_app(settings=isolated_settings())
    recorded = json.loads((FIXTURES / "routes.json").read_text(encoding="utf-8"))
    registered = [{
        "path": route.path, "name": route.name,
        "methods": sorted(getattr(route, "methods", None) or []),
        "include_in_schema": getattr(route, "include_in_schema", False),
    } for route in effective_routes(app.routes)]
    assert registered == recorded
    # 顺序本身属于契约：FastAPI 默认路由在前，业务路由按组合根的注册顺序展开。
    assert [entry["path"] for entry in registered[:4]] == [
        "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc",
    ]
    assert "get" in app.openapi()["paths"]["/health"]


class _StubRuntime:
    """替代真实 Runtime：只记录关闭与推荐图装配。"""

    def __init__(self) -> None:
        self.closed = False
        self.rec_graph = None

    async def aclose(self) -> None:
        self.closed = True


def test_factory_lifespan_closes_owned_resources(monkeypatch):
    """Runtime 只在 lifespan 中创建，且正常与异常退出都必须关闭。"""
    import ecom.main as main_module

    runtimes: list[_StubRuntime] = []
    graph = object()

    async def fake_create_runtime() -> _StubRuntime:
        runtime = _StubRuntime()
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(main_module, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(main_module, "build_recommendation_graph", lambda: graph)
    monkeypatch.setattr(main_module, "get_settings", isolated_settings)

    app = main_module.create_app(settings=isolated_settings())
    assert not hasattr(app.state, "runtime"), "工厂阶段不得创建 Runtime"

    async def normal_exit() -> None:
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            assert runtime is runtimes[0]
            assert runtime.rec_graph is graph
            assert runtime.closed is False
        assert runtime.closed is True

    asyncio.run(normal_exit())

    failing = main_module.create_app(settings=isolated_settings())

    async def failing_exit() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with failing.router.lifespan_context(failing):
                raise RuntimeError("boom")
        assert runtimes[-1].closed is True

    asyncio.run(failing_exit())
    assert len(runtimes) == 2


# 在干净解释器里隔离外部副作用：
# - 任何 socket 连接立即失败（含真实 LLM/Redis 调用）；
# - 客户端构造被允许（旧栈 `legacy/orchestrator/graph.py` 在导入期就构造 Agent 单例，
#   这是搬迁前既有的行为），但任何方法调用立即失败；
# - `create_runtime` 被替换为哨兵，只有 lifespan 允许调用它。
_IMPORT_ISOLATION_SCRIPT = r'''
import asyncio
import socket
import sys
import types


def _forbid(*args, **kwargs):
    raise AssertionError('network connection is not allowed here')


class _NoCallClient:
    """允许构造，调用任何方法即失败。"""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            raise AssertionError(f'{name} must not be called here')
        return forbidden


redis_package = types.ModuleType('redis')
redis_asyncio = types.ModuleType('redis.asyncio')
redis_asyncio.Redis = _NoCallClient
redis_package.asyncio = redis_asyncio
sys.modules['redis'] = redis_package
sys.modules['redis.asyncio'] = redis_asyncio

langchain_openai = types.ModuleType('langchain_openai')
langchain_openai.ChatOpenAI = _NoCallClient
sys.modules['langchain_openai'] = langchain_openai

socket.socket.connect = _forbid
socket.socket.connect_ex = _forbid
socket.create_connection = _forbid

import ecom.api.deps as deps


async def _forbidden_runtime():
    raise AssertionError('only lifespan may build the Runtime')


deps.create_runtime = _forbidden_runtime

import ecom.main as main_module

assert main_module.create_runtime is _forbidden_runtime, (
    '工厂必须使用依赖模块的 create_runtime'
)
assert not hasattr(main_module, 'app'), 'import ecom.main must not build an app'
from ecom.config import Settings

settings = Settings(
    _env_file=None, database_url='', llm_api_key='test-only-not-a-secret',
    identity_environment='production', commerce_demo_enabled=False,
)
application = main_module.create_app(settings=settings)
assert not hasattr(application.state, 'runtime'), 'factory must not build Runtime'
assert 'get' in application.openapi()['paths']['/health']


async def _lifespan_is_the_only_caller():
    try:
        async with application.router.lifespan_context(application):
            pass
    except AssertionError as error:
        assert 'lifespan' in str(error), str(error)
    else:
        raise AssertionError('lifespan must call create_runtime')


asyncio.run(_lifespan_is_the_only_caller())
print('imported-without-runtime-or-llm')
'''


def test_import_does_not_build_runtime_or_call_llm():
    """导入与工厂构造都不建 Runtime、不发连接、不调用模型/Redis 客户端。"""
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_ISOLATION_SCRIPT],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "imported-without-runtime-or-llm" in result.stdout


def test_demo_path_is_independent_of_route_depth(monkeypatch):
    """演示目录由组合根按仓库根传入，不随路由模块自身深度变化。"""
    import ecom.main as main_module

    captured: dict[str, Path] = {}

    def fake_configure(app, settings, *, demo_dir, **kwargs):
        captured["demo_dir"] = Path(demo_dir)

    monkeypatch.setattr(main_module, "configure_commerce_demo", fake_configure)
    main_module.create_app(settings=isolated_settings())

    demo_dir = captured["demo_dir"]
    assert demo_dir == ROOT / "demo"
    assert demo_dir == Path(main_module.__file__).resolve().parents[1] / "demo"
    assert (demo_dir / "commerce.html").is_file()

    route_module = importlib.import_module("ecom.api.routes.demo")
    route_source = Path(route_module.__file__).read_text(encoding="utf-8")
    assert not any(
        isinstance(node, ast.Name) and node.id == "__file__"
        for node in ast.walk(ast.parse(route_source))
    ), "路由模块不得按自身文件位置推算仓库目录"
    # 旧写法（按路由模块深度推 `ecom/api/demo`）会指向不存在的目录，本断言能抓住该回归。
    assert Path(route_module.__file__).resolve().parents[1] / "demo" != demo_dir


def test_registered_metadata_matches_pre_move():
    """ORM 注册结果与搬迁前冻结值逐字一致；只有模块名随包根重命名。"""
    from tests.test_layering_baseline import metadata_signature

    recorded = json.loads((FIXTURES / "metadata.json").read_text(encoding="utf-8"))
    current = metadata_signature()
    assert current["tables"] == recorded["tables"]
    assert current["modules"] == recorded["modules"]
    assert recorded["modules"] == [
        f"ecom.modules.{name}" for name in (
            "catalog", "pricing", "inventory", "identity", "sessions", "cart",
            "checkout", "events",
        )
    ]
