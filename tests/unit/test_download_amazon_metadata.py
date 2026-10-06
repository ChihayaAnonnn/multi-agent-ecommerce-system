"""下载器只访问选择的元数据文件，测试不连接网络。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "download_amazon_metadata",
    Path(__file__).parents[2] / "tools/amazon/download_amazon_metadata.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeApi:
    def dataset_info(self, repo_id, revision):
        assert repo_id == module.REPO_ID
        return SimpleNamespace(sha="a" * 40)


def test_default_download_is_only_six_metadata_files():
    args = module.parse_args([])
    paths = module.metadata_paths(args.categories)
    assert len(paths) == 6
    assert len(set(paths)) == 6
    assert all(p.startswith("raw/meta_categories/meta_") and p.endswith(".jsonl") for p in paths)
    assert "raw/meta_categories/meta_Electronics.jsonl" in paths
    assert not any("review" in p.lower() or "*" in p for p in paths)


def test_dry_run_never_downloads_or_writes_manifest(tmp_path):
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        assert kwargs["dry_run"] is True
        return [SimpleNamespace(filename=p, file_size=12, will_download=True)
                for p in kwargs["allow_patterns"]]

    args = module.parse_args(["--dry-run", "--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    module.run(args, FakeApi(), snapshot)
    assert len(calls) == 1
    assert calls[0]["allow_patterns"] == ["raw/meta_categories/meta_Pet_Supplies.jsonl"]
    assert not (tmp_path / "download_manifest.json").exists()


def test_success_records_revision_and_rerun_reuses_it(tmp_path):
    revisions = []

    def snapshot(**kwargs):
        revisions.append(kwargs["revision"])
        files = [SimpleNamespace(filename=p, file_size=12, will_download=True)
                 for p in kwargs["allow_patterns"]]
        if kwargs["dry_run"]:
            return files
        for f in files:
            path = tmp_path / f.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 12)
        return str(tmp_path)

    args = module.parse_args(["--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    module.run(args, FakeApi(), snapshot)

    class NoNetworkApi:
        def dataset_info(self, *args, **kwargs):
            pytest.fail("已有 manifest 应直接使用固定版本")

    module.run(args, NoNetworkApi(), snapshot)
    assert revisions == ["a" * 40] * 4
    assert '"status": "complete"' in (tmp_path / "download_manifest.json").read_text()


def test_failed_download_keeps_manifest_for_resume(tmp_path):
    def snapshot(**kwargs):
        if kwargs["dry_run"]:
            return [SimpleNamespace(filename=kwargs["allow_patterns"][0], file_size=12, will_download=True)]
        raise OSError("connection interrupted")

    args = module.parse_args(["--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    with pytest.raises(OSError, match="connection interrupted"):
        module.run(args, FakeApi(), snapshot)
    assert '"status": "downloading"' in (tmp_path / "download_manifest.json").read_text()


def test_invalid_category_rejected():
    with pytest.raises(SystemExit):
        module.parse_args(["--categories", "invalid"])


def test_missing_upstream_file_stops_before_download(tmp_path):
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        return []

    args = module.parse_args(["--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    with pytest.raises(ValueError, match="缺少所选文件"):
        module.run(args, FakeApi(), snapshot)
    assert len(calls) == 1
    assert not (tmp_path / "download_manifest.json").exists()


def test_wrong_file_size_does_not_mark_complete(tmp_path):
    def snapshot(**kwargs):
        filename = kwargs["allow_patterns"][0]
        if kwargs["dry_run"]:
            return [SimpleNamespace(filename=filename, file_size=12, will_download=True)]
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"short")

    args = module.parse_args(["--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    with pytest.raises(OSError, match="大小不符"):
        module.run(args, FakeApi(), snapshot)
    assert '"status": "downloading"' in (tmp_path / "download_manifest.json").read_text()


def test_insufficient_disk_space_stops_before_download(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))

    def snapshot(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(filename=kwargs["allow_patterns"][0], file_size=12, will_download=True)]

    args = module.parse_args(["--output-dir", str(tmp_path), "--categories", "Pet_Supplies"])
    with pytest.raises(OSError, match="可用空间"):
        module.run(args, FakeApi(), snapshot)
    assert len(calls) == 1
    assert not (tmp_path / "download_manifest.json").exists()
