"""Amazon 元数据导入测试共用的模块加载与样本构造。

纯桩用例（dry-run、归一化）与真实 MySQL 用例必须拿到同一个模块对象和
同一份原始记录，两侧各写一份会让「预览通过、落库失败」变得不可见。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def importer():
    # 延后导入，使 RED 明确显示缺失的实现，而非打断其他测试收集。
    return importlib.import_module('tools.amazon.import_amazon_metadata')


def record(asin='B000000001', title='测试耳机'):
    return {
        'parent_asin': asin, 'title': title, 'price': 24.95,
        'details': {'Brand': 'Acme'},
        'categories': ['Electronics', 'Headphones'], 'description': ['适合通勤'],
        'images': [{'large': 'https://example.com/item.jpg'}],
        'average_rating': 4.3, 'rating_number': 12,
    }


def make_source(tmp_path, rows) -> Path:
    root = tmp_path / 'source'
    path = root / 'raw/meta_categories/meta_Electronics.jsonl'
    path.parent.mkdir(parents=True)
    text = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
    path.write_text(text, encoding='utf-8')
    manifest = {
        'status': 'complete', 'repo_id': 'McAuley-Lab/Amazon-Reviews-2023',
        'revision': 'test',
        'files': {'raw/meta_categories/meta_Electronics.jsonl': {
            'size_bytes': path.stat().st_size}},
    }
    (root / 'download_manifest.json').write_text(json.dumps(manifest))
    return root
