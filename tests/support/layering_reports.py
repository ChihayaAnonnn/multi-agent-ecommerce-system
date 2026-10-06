"""分层迁移的执行身份、合同与新增 lint 告警门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

JsonObject = dict[str, Any]
CONTRACT_FILES = ("routes.json", "openapi.json", "metadata.json", "errors.json")


PathMapping = dict[str, "str | list[str]"]


def mapped_paths(path: str, path_map: PathMapping) -> tuple[str, ...]:
    """一个基线路径迁移后可能的全部目标；混合文件按依赖拆分时不止一个。"""
    normalized = path.replace("\\", "/")
    prefixes = (key for key in path_map if "::" not in key)
    for original in sorted(prefixes, key=len, reverse=True):
        prefix = original.rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            value = path_map[original]
            destinations = (value,) if isinstance(value, str) else tuple(value)
            return tuple(
                destination.rstrip("/") + normalized[len(prefix):]
                for destination in destinations
            )
    return (normalized,)


def mapped_path(path: str, path_map: PathMapping) -> str:
    paths = mapped_paths(path, path_map)
    if len(paths) != 1:
        raise ValueError(f"ambiguous path mapping: {path}")
    return paths[0]


def mapped_node_id(node_id: str, path_map: PathMapping) -> str:
    """整文件搬迁按前缀映射；混合文件拆分后按精确 node ID 覆盖。"""
    exact = path_map.get(node_id)
    if exact is not None:
        assert isinstance(exact, str), f"node mapping must be a single path: {node_id}"
        return exact
    path, separator, suffix = node_id.partition("::")
    return mapped_path(path, path_map) + separator + suffix


def _test_results(report_path: Path) -> dict[str, tuple[str, str]]:
    results = {}
    for case in ElementTree.parse(report_path).iter("testcase"):
        properties = {
            prop.get("name"): prop.get("value")
            for prop in case.findall("./properties/property")
        }
        node_id = properties.get("node_id")
        if not node_id:
            raise ValueError("JUnit testcase missing node_id property")
        if node_id in results:
            raise ValueError(f"duplicate node_id: {node_id}")
        outcome, reason = "passed", ""
        for tag in ("error", "failure", "skipped"):
            result = case.find(tag)
            if result is not None:
                outcome = tag
                if tag == "skipped":
                    reason = result.get("message") or (result.text or "").strip()
                break
        results[node_id] = (outcome, reason)
    return results


def _manifest(directory: Path) -> set[str]:
    document = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    node_ids = [item["node_id"] for item in document["tests"]]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("duplicate node_id in manifest")
    return set(node_ids)


def _report_consistency(
    label: str, manifest: set[str], results: dict[str, tuple[str, str]]
) -> list[str]:
    return [
        f"{label} unexecuted: {node_id}"
        for node_id in sorted(manifest - results.keys())
    ] + [
        f"{label} uncollected: {node_id}"
        for node_id in sorted(results.keys() - manifest)
    ]


def _compare_test_results(
    baseline_ids: set[str], current_ids: set[str],
    baseline: dict[str, tuple[str, str]], current: dict[str, tuple[str, str]],
    allowed: dict[str, str],
) -> list[str]:
    issues = []
    for node_id in sorted(baseline_ids | current_ids):
        if node_id in allowed:
            continue
        if node_id not in current_ids:
            issues.append(f"missing test: {node_id}")
        elif node_id not in baseline_ids:
            if current.get(node_id, ("unexecuted", ""))[0] != "passed":
                issues.append(f"added nonpassing test: {node_id}")
        elif baseline.get(node_id) != current.get(node_id):
            if current.get(node_id, ("unexecuted", ""))[0] != "passed":
                issues.append(f"result or skip reason changed: {node_id}")
    return issues


def _compare_contracts(
    baseline_dir: Path, current_dir: Path, allowed: dict[str, str]
) -> list[str]:
    issues = []
    for name in CONTRACT_FILES:
        original, latest = baseline_dir / name, current_dir / name
        if name in allowed or not (original.exists() or latest.exists()):
            continue
        if not (original.exists() and latest.exists()):
            issues.append(f"contract missing: {name}")
        elif json.loads(original.read_text()) != json.loads(latest.read_text()):
            issues.append(f"contract changed: {name}")
    return issues


def compare_results(
    baseline_dir: Path,
    current_dir: Path,
    path_map: dict[str, str],
    allowed_changes: dict[str, str],
) -> list[str]:
    for approval in allowed_changes.values():
        if not re.fullmatch(r"(?:R|L)\d+\s*[:：]\s*\S.*", approval):
            raise ValueError("allowed change requires approval ID and reason")
    baseline = {
        mapped_node_id(node_id, path_map): result
        for node_id, result in _test_results(baseline_dir / "results.xml").items()
    }
    baseline_ids = {
        mapped_node_id(node_id, path_map) for node_id in _manifest(baseline_dir)
    }
    if len(baseline_ids) != len(_manifest(baseline_dir)):
        raise ValueError("path mapping collapses baseline node IDs")
    current = _test_results(current_dir / "results.xml")
    current_ids = _manifest(current_dir)
    allowed = {
        mapped_node_id(node_id, path_map): reason
        for node_id, reason in allowed_changes.items()
    }
    issues = _report_consistency("baseline", baseline_ids, baseline)
    issues += _report_consistency("current", current_ids, current)
    issues += _compare_test_results(
        baseline_ids, current_ids, baseline, current, allowed
    )
    issues += _compare_contracts(baseline_dir, current_dir, allowed_changes)
    return issues


def compare_ci_results(
    baseline_dir: Path, report_dirs: list[Path], path_map: dict[str, str],
    *, contracts: bool = True,
) -> list[str]:
    """三类 CI 报告必须共同保留已验证全通过基线的全部测试身份。

    `contracts=False` 供只保存清单与执行证据的基线目录使用（例如阶段一
    714 节点证据）：这类目录刻意不含合同文件，不能拿当前合同冒充原始证据。
    """
    original_ids = _manifest(baseline_dir)
    exceptions = documented_exceptions(baseline_dir)
    baseline_ids = {
        mapped_node_id(node_id, path_map) for node_id in original_ids - exceptions
    }
    if len(baseline_ids) != len(original_ids - exceptions):
        raise ValueError("path mapping collapses baseline node IDs")
    baseline = {node_id: ("passed", "") for node_id in baseline_ids}
    current_ids = set()
    current = {}
    issues = []
    for directory in report_dirs:
        manifest = _manifest(directory)
        results = _test_results(directory / "results.xml")
        if current_ids & manifest or current.keys() & results.keys():
            raise ValueError("duplicate node_id across CI jobs")
        issues += _report_consistency(directory.name, manifest, results)
        current_ids.update(manifest)
        current.update(results)
    issues += _compare_test_results(baseline_ids, current_ids, baseline, current, {})
    if contracts:
        issues += _compare_contracts(baseline_dir, report_dirs[0], {})
    return issues


def documented_exceptions(baseline_dir: Path) -> set[str]:
    """基线目录自带的逐条例外（例如阶段一布局断言变化），必须写明理由。"""
    path = baseline_dir / "exceptions.json"
    if not path.exists():
        return set()
    exceptions = json.loads(path.read_text(encoding="utf-8"))
    if not all(
        isinstance(reason, str) and reason.strip() for reason in exceptions.values()
    ):
        raise ValueError("baseline exceptions require a reason")
    return set(exceptions)


def assert_zero_skips(report_path: Path) -> None:
    cases = list(ElementTree.parse(report_path).iter("testcase"))
    assert cases, "empty integration report"
    skipped = [
        case.get("name", "") for case in cases if case.find("skipped") is not None
    ]
    assert not skipped, f"skipped integration tests: {', '.join(skipped)}"
    failed = [
        case.get("name", "") for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    assert not failed, f"failed integration tests: {', '.join(failed)}"


def normalize_lint_records(records: list[JsonObject], root: Path) -> list[JsonObject]:
    normalized = []
    root = root.resolve()
    for record in records:
        filename = Path(record["filename"])
        relative = filename.relative_to(root) if filename.is_absolute() else filename
        source = record.get("source")
        if source is None:
            lines = (root / relative).read_text(encoding="utf-8").splitlines()
            start = record["location"]["row"] - 1
            end = record.get("end_location", record["location"])["row"]
            source = "sha256:" + hashlib.sha256(
                "\n".join(lines[start:end]).encode("utf-8")
            ).hexdigest()
        normalized.append({
            "filename": relative.as_posix(), "code": record["code"],
            "source": source, "location": record["location"],
        })
    return normalized


def new_lint_violations(
    baseline: list[JsonObject], current: list[JsonObject], path_map: PathMapping
) -> list[JsonObject]:
    baseline = normalize_lint_records(baseline, Path.cwd())
    current = normalize_lint_records(current, Path.cwd())

    def identity(record: JsonObject, path: str) -> tuple[str, str, str]:
        return (path, record["code"], record["source"])

    # 一条基线告警只有一次共享额度：混合文件拆分后，多个目标只是这条额度的
    # 候选位置。若给每个目标各记一份，同一告警在两个目标各出现一次也会被
    # 判定为「不是新增」，等于把历史额度复制了一份。
    remaining: Counter = Counter()
    candidates: dict[tuple[str, str, str], list[tuple[str, str, str]]] = {}
    ordered = sorted(
        baseline, key=lambda item: (item["filename"], item["code"], item["source"])
    )
    for record in ordered:
        group = identity(record, record["filename"])
        remaining[group] += 1
        for destination in mapped_paths(record["filename"], path_map):
            entries = candidates.setdefault(identity(record, destination), [])
            if group not in entries:
                entries.append(group)
    added = []
    for record in current:
        key = identity(record, record["filename"])
        for group in candidates.get(key, ()):
            if remaining[group]:
                remaining[group] -= 1
                break
        else:
            added.append(record)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    lint = subcommands.add_parser("lint")
    lint.add_argument("--baseline", type=Path, required=True)
    lint.add_argument("--current", type=Path, required=True)
    lint.add_argument("--path-map", type=Path)
    zero = subcommands.add_parser("zero-skips")
    zero.add_argument("report", type=Path)
    compare = subcommands.add_parser("compare-ci")
    compare.add_argument("--baseline", type=Path, required=True)
    for category in ("unit", "mysql", "redis"):
        compare.add_argument(f"--{category}", type=Path, required=True)
    compare.add_argument("--path-map", type=Path)
    compare.add_argument(
        "--no-contracts", action="store_true",
        help="基线目录只含清单与执行证据时跳过合同比较",
    )
    args = parser.parse_args()
    try:
        if args.command == "zero-skips":
            assert_zero_skips(args.report)
            print("integration report: zero skips, zero failures")
            return 0
        if args.command == "compare-ci":
            path_map = json.loads(args.path_map.read_text()) if args.path_map else {}
            issues = compare_ci_results(
                args.baseline, [args.unit, args.mysql, args.redis], path_map,
                contracts=not args.no_contracts,
            )
            for issue in issues:
                print(issue)
            if not issues:
                print("CI merged report: all baseline tests retained; no regressions")
            return 1 if issues else 0
        root = Path.cwd()
        baseline = normalize_lint_records(json.loads(args.baseline.read_text()), root)
        current = normalize_lint_records(json.loads(args.current.read_text()), root)
        path_map = json.loads(args.path_map.read_text()) if args.path_map else {}
        added = new_lint_violations(baseline, current, path_map)
        for record in added:
            print(f"{record['filename']}:{record['location']['row']}: {record['code']}")
        print(f"新增 lint 告警：{len(added)}")
        return 1 if added else 0
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, ElementTree.ParseError):
        print("layering report tool error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
