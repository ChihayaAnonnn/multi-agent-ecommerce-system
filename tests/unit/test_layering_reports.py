"""分层迁移的报告比较和门禁回归测试。"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest


def report_tools():
    from tests.support import layering_reports

    return layering_reports


def write_report(directory: Path, cases: list[tuple[str, str, str]]) -> None:
    directory.mkdir()
    testcases = []
    for node_id, outcome, reason in cases:
        child = "" if outcome == "passed" else (
            f'<{outcome} message="{escape(reason)}"/>'
        )
        testcases.append(
            f'<testcase name="test"><properties><property name="node_id" '
            f'value="{escape(node_id)}"/></properties>{child}</testcase>'
        )
    (directory / "results.xml").write_text(
        "<testsuite>" + "".join(testcases) + "</testsuite>", encoding="utf-8"
    )
    (directory / "manifest.json").write_text(
        json.dumps({"tests": [{"node_id": case[0]} for case in cases]}),
        encoding="utf-8",
    )


def test_equal_skip_count_does_not_hide_missing_test(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    write_report(baseline, [("tests/test_old.py::test_one", "skipped", "no db")])
    write_report(current, [("tests/test_new.py::test_two", "skipped", "no db")])
    issues = report_tools().compare_results(baseline, current, {}, {})
    assert any("missing" in issue and "test_one" in issue for issue in issues)


def test_zero_skip_gate_rejects_skip(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuite><testcase name="required"><skipped/></testcase></testsuite>'
    )
    with pytest.raises(AssertionError, match="skipped"):
        report_tools().assert_zero_skips(report)


def test_zero_skip_gate_accepts_executed_tests(tmp_path):
    write_report(tmp_path / "run", [("tests/test_ok.py::test_ok", "passed", "")])
    report_tools().assert_zero_skips(tmp_path / "run" / "results.xml")


def test_zero_skip_gate_rejects_empty_report(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("<testsuite/>")
    with pytest.raises(AssertionError, match="empty"):
        report_tools().assert_zero_skips(report)


def test_path_mapping_preserves_parametrized_node_ids(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    suffix = "::TestCase::test_one[param::value/with.py]"
    write_report(baseline, [("tests/test_old.py" + suffix, "passed", "")])
    write_report(current, [("tests/unit/test_old.py" + suffix, "passed", "")])
    assert report_tools().compare_results(
        baseline, current, {"tests/test_old.py": "tests/unit/test_old.py"}, {}
    ) == []


@pytest.mark.parametrize(
    "outcome,reason", [("skipped", "no redis"), ("failure", "bad")]
)
def test_result_and_skip_reason_changes_are_rejected(tmp_path, outcome, reason):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    node_id = "tests/test_one.py::test_one"
    write_report(baseline, [(node_id, "skipped", "no db")])
    write_report(current, [(node_id, outcome, reason)])
    assert report_tools().compare_results(baseline, current, {}, {})


def test_allowed_change_requires_approval_and_reason(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    node_id = "tests/test_one.py::test_one"
    write_report(baseline, [(node_id, "passed", "")])
    write_report(current, [(node_id, "skipped", "no db")])
    with pytest.raises(ValueError, match="approval"):
        report_tools().compare_results(baseline, current, {}, {node_id: "because"})
    assert report_tools().compare_results(
        baseline, current, {}, {node_id: "R12: 明确批准的测试变化"}
    ) == []


def test_manifest_catches_unexecuted_test(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    case = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [case])
    write_report(current, [case])
    (current / "results.xml").write_text("<testsuite/>")
    assert any(
        "unexecuted" in issue
        for issue in report_tools().compare_results(baseline, current, {}, {})
    )


def test_contract_difference_is_reported(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    case = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [case])
    write_report(current, [case])
    (baseline / "routes.json").write_text('["/health"]')
    (current / "routes.json").write_text('["/changed"]')
    assert "contract changed: routes.json" in report_tools().compare_results(
        baseline, current, {}, {}
    )


def lint_entry(path: str, source: str, row: int = 1) -> dict:
    return {
        "filename": path, "code": "E501", "source": source,
        "location": {"row": row, "column": 89},
    }


def test_added_lint_occurrence_is_not_hidden():
    occurrence = lint_entry("tests/test_one.py", "long source")
    assert report_tools().new_lint_violations(
        [occurrence], [occurrence, occurrence], {}
    ) == [occurrence]


def test_lint_path_mapping_ignores_moved_line_numbers():
    baseline = [lint_entry("catalog/service.py", "long source", 10)]
    current = [lint_entry("ecom/modules/catalog/service.py", "long source", 40)]
    assert report_tools().new_lint_violations(
        baseline, current, {"catalog": "ecom/modules/catalog"}
    ) == []


def test_lint_different_source_is_new_violation():
    assert report_tools().new_lint_violations(
        [lint_entry("one.py", "old source")],
        [lint_entry("one.py", "new source")], {},
    )


def test_ci_compare_uses_documented_baseline_exceptions(tmp_path):
    """基线目录自带的例外只对写明的 node 生效，且必须带理由。"""
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    write_report(baseline, [("tests/test_old.py::test_one", "passed", "")])
    write_report(current, [("tests/test_old.py::test_two", "passed", "")])
    tools = report_tools()
    issues = tools.compare_ci_results(baseline, [current], {}, contracts=False)
    assert any("missing" in issue and "test_one" in issue for issue in issues)
    (baseline / "exceptions.json").write_text(
        json.dumps({"tests/test_old.py::test_one": "参数集收敛，已合并为另一用例"}),
        encoding="utf-8",
    )
    assert tools.compare_ci_results(baseline, [current], {}, contracts=False) == []


def test_ci_compare_rejects_reasonless_exceptions(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    write_report(baseline, [("tests/test_old.py::test_one", "passed", "")])
    write_report(current, [("tests/test_old.py::test_one", "passed", "")])
    (baseline / "exceptions.json").write_text(
        json.dumps({"tests/test_old.py::test_one": "   "}), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reason"):
        report_tools().compare_ci_results(baseline, [current], {}, contracts=False)


def test_split_file_shares_one_baseline_allowance():
    """一拆二时，同一告警在两个目标各出现一次 => 其中一条是新增。"""
    baseline = [lint_entry("legacy/one.py", "long source")]
    current = [
        lint_entry("unit/one.py", "long source"),
        lint_entry("integration/one.py", "long source"),
    ]
    path_map = {"legacy/one.py": ["unit/one.py", "integration/one.py"]}
    assert report_tools().new_lint_violations(baseline, current, path_map) == [
        current[1]
    ]


def test_split_file_keeps_one_occurrence_per_baseline_record():
    """原告警有两条时，两侧各一条仍不算新增（额度按基线记录数共享）。"""
    baseline = [lint_entry("legacy/one.py", "long source")] * 2
    current = [
        lint_entry("unit/one.py", "long source"),
        lint_entry("integration/one.py", "long source"),
    ]
    path_map = {"legacy/one.py": ["unit/one.py", "integration/one.py"]}
    assert report_tools().new_lint_violations(baseline, current, path_map) == []


def test_split_file_reports_increased_occurrences():
    """目标侧次数增加时，多出来的算新增。"""
    baseline = [lint_entry("legacy/one.py", "long source")]
    current = [
        lint_entry("unit/one.py", "long source"),
        lint_entry("unit/one.py", "long source", row=2),
    ]
    path_map = {"legacy/one.py": ["unit/one.py", "integration/one.py"]}
    assert len(report_tools().new_lint_violations(baseline, current, path_map)) == 1


def test_split_file_result_is_independent_of_target_order():
    """目标 list 的顺序不影响判定结果。"""
    baseline = [lint_entry("legacy/one.py", "long source")]
    current = [
        lint_entry("unit/one.py", "long source"),
        lint_entry("integration/one.py", "long source"),
    ]
    forward = {"legacy/one.py": ["unit/one.py", "integration/one.py"]}
    reverse = {"legacy/one.py": ["integration/one.py", "unit/one.py"]}
    assert report_tools().new_lint_violations(baseline, current, forward) == (
        report_tools().new_lint_violations(baseline, current, reverse)
    )


def test_merged_target_consumes_each_baseline_record():
    """合并方向（多对一）仍按基线记录逐条消费，不误报新增。"""
    baseline = [
        lint_entry("legacy/a.py", "long source"),
        lint_entry("legacy/b.py", "long source"),
    ]
    current = [
        lint_entry("merged/one.py", "long source"),
        lint_entry("merged/one.py", "long source", row=2),
    ]
    path_map = {"legacy/a.py": "merged/one.py", "legacy/b.py": "merged/one.py"}
    assert report_tools().new_lint_violations(baseline, current, path_map) == []


def test_fixture_classification_uses_closure_not_filename():
    from tests import conftest

    assert conftest.integration_markers(
        ["tmp_path", "clean_redis", "redis_test_url"]
    ) == {"redis"}
    assert conftest.integration_markers(["mysql_test_database_url", "helper"]) == {
        "mysql"
    }
    assert conftest.integration_markers(["tmp_path"]) == set()


def test_raw_ruff_records_compare_source_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "one.py"
    source.write_text("import unused\n", encoding="utf-8")
    raw = [{
        "filename": str(source), "code": "F401",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 14},
    }]
    baseline = report_tools().normalize_lint_records(raw, tmp_path)
    assert report_tools().new_lint_violations(baseline, raw, {}) == []


def test_lint_normalization_does_not_store_source_secrets(tmp_path):
    source = tmp_path / "one.py"
    source.write_text("synthetic_sensitive_literal\n", encoding="utf-8")
    normalized = report_tools().normalize_lint_records([{
        "filename": str(source), "code": "F401",
        "location": {"row": 1, "column": 1},
    }], tmp_path)
    assert normalized[0]["filename"] == "one.py"
    assert normalized[0]["source"].startswith("sha256:")
    assert "synthetic_sensitive_literal" not in json.dumps(normalized)


@pytest.mark.parametrize("mode,expected", [("clean", 0), ("added", 1), ("broken", 2)])
def test_lint_cli_distinguishes_gate_and_tool_errors(tmp_path, mode, expected):
    baseline, current = tmp_path / "baseline.json", tmp_path / "current.json"
    occurrence = lint_entry("one.py", "source")
    baseline.write_text(json.dumps([occurrence]))
    current.write_text(
        "not-json" if mode == "broken"
        else json.dumps([occurrence] * (2 if mode == "added" else 1))
    )
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.layering_reports", "lint",
         "--baseline", str(baseline), "--current", str(current)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == expected


def test_require_gate_checks_only_selected_categories(monkeypatch):
    from tests import conftest

    monkeypatch.delenv("ECOM_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("ECOM_TEST_REDIS_URL", "synthetic-test-setting")
    config = SimpleNamespace(
        getoption=lambda name: name == "--require-integration-services"
    )
    session = SimpleNamespace(
        config=config, items=[SimpleNamespace(fixturenames=["redis_test_url"])]
    )
    conftest.pytest_collection_finish(session)
    session.items = [SimpleNamespace(fixturenames=["mysql_test_database_url"])]
    with pytest.raises(pytest.UsageError, match="ECOM_TEST_DATABASE_URL"):
        conftest.pytest_collection_finish(session)


@pytest.mark.parametrize("category", ["mysql", "redis"])
def test_require_fixtures_never_use_development_fallback(monkeypatch, category):
    from tests import conftest

    monkeypatch.delenv("ECOM_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("ECOM_TEST_REDIS_URL", raising=False)
    resolver = (
        "test_database_url_from_settings" if category == "mysql"
        else "test_redis_url_from_settings"
    )
    monkeypatch.setattr(conftest, resolver, lambda: "synthetic-development-setting")
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: True))
    fixture = (
        conftest.mysql_test_database_url if category == "mysql"
        else conftest.redis_test_url
    )
    with pytest.raises(pytest.skip.Exception, match="未配置"):
        fixture.__wrapped__(request)


def test_added_passing_test_is_allowed(tmp_path):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    original = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [original])
    write_report(current, [original, ("tests/test_two.py::test_two", "passed", "")])
    assert report_tools().compare_results(baseline, current, {}, {}) == []


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_added_nonpassing_test_is_rejected(tmp_path, outcome):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    original = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [original])
    write_report(current, [original, ("tests/test_two.py::test_two", outcome, "bad")])
    assert report_tools().compare_results(baseline, current, {}, {})


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_improved_result_is_allowed(tmp_path, outcome):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    node_id = "tests/test_one.py::test_one"
    write_report(baseline, [(node_id, outcome, "old issue")])
    write_report(current, [(node_id, "passed", "")])
    assert report_tools().compare_results(baseline, current, {}, {}) == []


def test_ci_merge_detects_original_test_missing_from_all_jobs(tmp_path):
    baseline = tmp_path / "baseline"
    original = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [original])
    jobs = [tmp_path / category for category in ("unit", "mysql", "redis")]
    for index, directory in enumerate(jobs):
        write_report(directory, [(f"tests/new.py::test_new_{index}", "passed", "")])
    issues = report_tools().compare_ci_results(baseline, jobs, {})
    assert any("missing" in issue and "test_one" in issue for issue in issues)


def test_ci_merge_compares_disjoint_jobs_with_full_baseline(tmp_path):
    baseline = tmp_path / "baseline"
    cases = [(f"tests/one.py::test_{index}", "passed", "") for index in range(3)]
    write_report(baseline, cases)
    (baseline / "results.xml").unlink()
    jobs = [tmp_path / category for category in ("unit", "mysql", "redis")]
    for directory, case in zip(jobs, cases):
        write_report(directory, [case])
    assert report_tools().compare_ci_results(baseline, jobs, {}) == []


def test_ci_merge_rejects_overlapping_job_selections(tmp_path):
    baseline = tmp_path / "baseline"
    original = ("tests/test_one.py::test_one", "passed", "")
    write_report(baseline, [original])
    jobs = [tmp_path / category for category in ("unit", "mysql", "redis")]
    for directory in jobs:
        write_report(directory, [original])
    with pytest.raises(ValueError, match="duplicate"):
        report_tools().compare_ci_results(baseline, jobs, {})
