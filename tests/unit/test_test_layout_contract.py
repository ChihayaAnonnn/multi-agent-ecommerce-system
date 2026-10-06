"""tests 分类与离线工具搬迁的布局合同。

三类证据：原 node 身份在新布局里一一对应、离线工具 --help 不碰网络与数据库、
根 conftest 的仓库根解析与夹具作用域不因搬迁而改变。
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "layering"
PATH_MAP = FIXTURES / "path-map.json"
MANIFEST = FIXTURES / "manifest.json"
TOOLS = (
    "tools.amazon.download_amazon_metadata",
    "tools.amazon.import_amazon_metadata",
    "tools.reset_demo_password",
)
# 迁移前已实际执行的三类分片数量（.artifacts/layering/task-16 的 before-*.xml）。
# 分类只允许把用例移入正确分片，不允许任何分片变少。
SHARD_FLOORS = {"unit": 545, "mysql": 151, "redis": 29}
COLLECTED = re.compile(r"tests/.+\.py::.+")


def collect(marker: str | None = None) -> list[str]:
    """在子进程里真实收集，避免复用当前会话的选择或被外部 -m 过滤。"""
    command = [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
               "-p", "no:cacheprovider", "--no-header"]
    if marker is not None:
        command += ["-m", marker]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    node_ids = [line.strip() for line in result.stdout.splitlines()
                if COLLECTED.fullmatch(line.strip())]
    assert node_ids, f"empty collection for {marker!r}"
    return node_ids


@functools.lru_cache(maxsize=1)
def shards() -> dict[str, list[str]]:
    return {
        "unit": collect("not mysql and not redis"),
        "mysql": collect("mysql and not redis"),
        "redis": collect("redis"),
    }


def test_every_original_node_has_a_mapped_successor():
    from tests.support.layering_reports import mapped_node_id

    path_map = json.loads(PATH_MAP.read_text(encoding="utf-8"))
    original = [item["node_id"] for item in
                json.loads(MANIFEST.read_text(encoding="utf-8"))["tests"]]
    mapped = [mapped_node_id(node_id, path_map) for node_id in original]
    assert len(set(mapped)) == len(mapped), "路径映射把两个原 node 合并到同一位置"

    current = set(collect())
    assert len(current) >= len(original)
    assert [node_id for node_id in mapped if node_id not in current] == []


def test_marker_shards_stay_mutually_exclusive_and_complete():
    shards_by_name = shards()
    for name, node_ids in shards_by_name.items():
        assert len(node_ids) >= SHARD_FLOORS[name], name
        assert len(set(node_ids)) == len(node_ids), f"{name} 分片内部重复收集"
    names = sorted(shards_by_name)
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            overlap = set(shards_by_name[name]) & set(shards_by_name[other])
            assert not overlap, f"{name} 与 {other} 标记不互斥：{sorted(overlap)[:3]}"
    union = set().union(*(set(node_ids) for node_ids in shards_by_name.values()))
    assert union == set(collect()), "三类分片的并集不再是全集"


def test_tool_help_does_not_access_network_or_database():
    offline = {
        **os.environ,
        "ECOM_DATABASE_URL": "mysql+asyncmy://offline:offline@127.0.0.1:1/offline",
        "ECOM_TEST_DATABASE_URL": "mysql+asyncmy://offline:offline@127.0.0.1:1/offline",
        "ECOM_REDIS_URL": "redis://127.0.0.1:1/0",
        "HF_HUB_OFFLINE": "1",
    }
    for module in TOOLS:
        result = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=60, env=offline,
        )
        assert result.returncode == 0, f"{module}: {result.stdout}{result.stderr}"
        assert "usage" in result.stdout.lower(), module


def conftest_fixture_scopes(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call else decorator
            if not (isinstance(target, ast.Attribute) and target.attr == "fixture"):
                continue
            keywords = call.keywords if call else []
            keyword = next((item for item in keywords if item.arg == "scope"), None)
            scopes[node.name] = keyword.value.value if keyword else "function"
    return scopes


def test_fixture_scope_and_root_paths_survive_move():
    from tests.conftest import REPO_ROOT

    assert ROOT == REPO_ROOT
    assert (REPO_ROOT / "alembic.ini").is_file()
    assert (REPO_ROOT / "migrations").is_dir()
    assert (REPO_ROOT / "ecom").is_dir()
    moved = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("conftest.py")
    )
    assert moved == ["tests/conftest.py"], "夹具定义只允许留在根 conftest"
    scopes = conftest_fixture_scopes(REPO_ROOT / "tests" / "conftest.py")
    assert scopes["mysql_test_database_url"] == "session"
    assert scopes["redis_test_url"] == "session"
    assert scopes["migrated_mysql_database"] == "function"
    assert scopes["clean_redis"] == "function"
