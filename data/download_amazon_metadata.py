#!/usr/bin/env python3
"""下载 Amazon Reviews 2023 的六类商品元数据；不下载评论或图片。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable

REPO_ID = "McAuley-Lab/Amazon-Reviews-2023"
CATEGORIES = (
    "Electronics",
    "Home_and_Kitchen",
    "Beauty_and_Personal_Care",
    "Sports_and_Outdoors",
    "Pet_Supplies",
    "Grocery_and_Gourmet_Food",
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "amazon_reviews_2023"


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--categories", nargs="+", choices=CATEGORIES, default=list(CATEGORIES),
                        help="下载品类，默认全部六类；名称区分大小写")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="数据保存目录")
    parser.add_argument("--workers", type=positive_integer, default=2, help="并行文件数，默认 2")
    parser.add_argument("--dry-run", action="store_true", help="仅查看文件和大小，不下载商品数据")
    return parser.parse_args(argv)


def metadata_paths(categories: list[str]) -> list[str]:
    return [f"raw/meta_categories/meta_{category}.jsonl" for category in dict.fromkeys(categories)]


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace, api: Any, snapshot: Callable[..., Any]) -> None:
    output = args.output_dir.expanduser().resolve()
    manifest_path = output / "download_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if manifest and manifest.get("repo_id") != REPO_ID:
        raise ValueError("目标目录的 manifest 来自其他数据源，请选择新目录")
    revision = manifest.get("revision") or api.dataset_info(REPO_ID, revision="main").sha
    if not revision:
        raise ValueError("数据源未返回版本号，无法固定下载版本")
    options = dict(repo_id=REPO_ID, repo_type="dataset", revision=revision,
                   allow_patterns=metadata_paths(args.categories), local_dir=str(output),
                   max_workers=args.workers)
    files = snapshot(**options, dry_run=True)
    expected = set(options["allow_patterns"])
    if {f.filename for f in files} != expected:
        raise ValueError("数据源缺少所选文件，已停止下载")
    if any(f.file_size is None for f in files):
        raise ValueError("数据源未提供完整文件大小，已停止下载")
    total = sum(f.file_size for f in files)
    remaining = sum(f.file_size for f in files if f.will_download)
    print(f"数据源：{REPO_ID}\n固定版本：{revision}\n保存目录：{output}", flush=True)
    for f in files:
        status = "待下载或续传" if f.will_download else "本地已有"
        print(f"  {f.filename}: {f.file_size / 1e9:.2f} GB（{status}）")
    print(f"共 {len(files)} 个文件，{total / 1e9:.2f} GB；待处理文件大小 {remaining / 1e9:.2f} GB。", flush=True)
    if args.dry_run:
        print("预览完成，未下载商品数据。")
        return
    output.mkdir(parents=True, exist_ok=True)
    # SDK 的待下载大小包含整个部分下载文件，因此这里采用保守空间检查。
    free = shutil.disk_usage(output).free
    if free < remaining:
        raise OSError(f"可用空间 {free / 1e9:.2f} GB 小于待处理文件大小 {remaining / 1e9:.2f} GB")
    manifest.update(repo_id=REPO_ID, revision=revision, status="downloading")
    save_manifest(manifest_path, manifest)
    snapshot(**options, dry_run=False)
    records = manifest.setdefault("files", {})
    for f in files:
        path = output / f.filename
        if not path.is_file() or path.stat().st_size != f.file_size:
            raise OSError(f"下载文件缺失或大小不符：{f.filename}；请重试原命令")
        records[f.filename] = {"size_bytes": f.file_size}
    manifest["status"] = "complete"
    save_manifest(manifest_path, manifest)
    print(f"下载完成：{len(files)} 个所选文件大小检查通过。\n文件目录：{output / 'raw/meta_categories'}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # 在导入 SDK 前设置，用户已有环境设置优先；缓存也放在 data/ 下。
    os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent / ".hf_cache"))
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)
    try:
        from huggingface_hub import HfApi, snapshot_download
        from huggingface_hub.utils import enable_progress_bars
    except ImportError:
        print("缺少下载依赖。请先运行：python -m pip install -r data/requirements.txt", file=sys.stderr)
        return 1
    enable_progress_bars()
    try:
        run(args, HfApi(), snapshot_download)
    except KeyboardInterrupt:
        print("\n下载已中断；保留缓存和部分文件，重新运行同一命令可继续。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"下载未完成：{exc}\n请检查网络、磁盘空间后重试同一命令；不要删除缓存或部分文件。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
