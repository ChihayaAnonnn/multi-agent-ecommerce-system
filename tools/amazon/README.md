# 商品元数据下载

下载 Amazon Reviews 2023 的六类商品元数据，用于后续构建日常购物场景和测试 Agent。脚本只下载指定的 JSONL 文件，不下载评论、图片二进制文件，也不会调用 LLM、导入数据库或生成向量。

使用 Hugging Face 官方 `huggingface_hub` 下载客户端，复用其进度条、缓存和断点续传能力，避免自行维护 HTTP 大文件下载逻辑。代价是需要安装独立下载依赖，并且下载时需要能够访问 Hugging Face。

## 在新终端开始下载

需要 Python 3.10 或更新版本。以下命令从项目根目录执行：

```bash
cd /Users/vivix/Desktop/workspace/multi-agent-ecommerce-system
python3 -m venv data/.venv
data/.venv/bin/python -m pip install -r tools/amazon/requirements.txt

# 可选：仅查询文件大小，不下载商品数据
data/.venv/bin/python -m tools.amazon.download_amazon_metadata --dry-run

# 开始下载默认的六个品类
data/.venv/bin/python -m tools.amazon.download_amazon_metadata
```

无需激活虚拟环境；该环境与应用的 `.venv` 分开。公开数据集通常无需登录。终端显示文件下载/总体进度条以及数据大小。初始获取文件信息时可能短暂没有下载进度。

默认约 **27 GB**（十进制），以 `--dry-run` 的实时结果为准。建议至少留出 **35 GB** 空间，另为后续清洗和导入预留空间。JSONL 已是可读文本，无需解压。脚本会保守检查待处理文件的空间；部分下载文件的完整大小也计入检查。

## 下载范围

| 品类参数 | 场景 | 大小参考 |
| --- | --- | --- |
| `Electronics` | 数码、电子产品 | 5.25 GB |
| `Home_and_Kitchen` | 家居、厨房 | 11.79 GB |
| `Beauty_and_Personal_Care` | 美妆、个护 | 2.84 GB |
| `Sports_and_Outdoors` | 运动、户外 | 4.13 GB |
| `Pet_Supplies` | 宠物用品 | 1.57 GB |
| `Grocery_and_Gourmet_Food` | 食品、杂货 | 1.38 GB |

这些是历史商品资料，主要为英文；图片字段提供 URL。价格可能缺失或过时，不能直接视为当前价格、人民币价格或真实库存。下载完成后仍需另外设计清洗、抽样和模拟库存/价格流程，才能供本系统检索和交易测试使用。

## 保存位置与续传

脚本已从 `data/` 迁到 `tools/amazon/`，下载数据、缓存与工具环境仍留在 `data/`。
默认目录按仓库标记定位，从其他工作目录启动也不会改变保存位置：

```text
data/
├── .venv/                              # 下载工具环境（与应用的 .venv 分开）
├── .hf_cache/                          # SDK 全局缓存，已有 HF_HOME 设置则沿用
└── amazon_reviews_2023/
    ├── download_manifest.json          # 固定数据源版本、文件大小、下载状态
    ├── .cache/huggingface/             # SDK 本地元信息、锁及下载临时文件
    └── raw/meta_categories/
        ├── meta_Electronics.jsonl
        ├── meta_Home_and_Kitchen.jsonl
        ├── meta_Beauty_and_Personal_Care.jsonl
        ├── meta_Sports_and_Outdoors.jsonl
        ├── meta_Pet_Supplies.jsonl
        └── meta_Grocery_and_Gourmet_Food.jsonl
```

按 `Ctrl+C` 可以停止。网络中断后，**重新运行原命令**即可利用 SDK 缓存继续；已有完成文件会复用。不要删除 `.cache/huggingface/`、临时文件或 `download_manifest.json`。首次实际下载会将源仓库 commit 固定在 manifest 中，后续运行继续使用该版本，避免上游更新造成重下。预览不会写入 manifest。

脚本结束时检查所选文件是否存在、大小是否与源数据一致。manifest 的 `complete` 表示最近一次所选范围下载成功；其中 `files` 记录历次成功下载的文件，不表示六个品类一定全部下载过。大小检查不能代替全量内容校验。

默认数据、缓存和工具环境已加入 `.gitignore`。不要同时运行两个下载进程写同一个输出目录；若使用自定义目录，请自行确认其 Git 忽略规则。

## 可选参数

先下载两个较小品类：

```bash
data/.venv/bin/python -m tools.amazon.download_amazon_metadata \
  --categories Pet_Supplies Grocery_and_Gourmet_Food
```

之后运行默认命令会补齐剩余品类。品类名称区分大小写。

更改保存目录或并行文件数（默认同时下载 2 个文件）：

```bash
data/.venv/bin/python -m tools.amazon.download_amazon_metadata \
  --output-dir /Volumes/Data/amazon_reviews_2023 --workers 1
```

网络不稳定时降低 `--workers`。磁盘不足时换一个输出目录；原目录的部分下载文件不会自动迁移。脚本成功退出码为 `0`，失败为 `1`，用户中断为 `130`；失败时保留下载现场。

遇到超时可增大 SDK 超时，或配置网络代理：

```bash
HF_HUB_DOWNLOAD_TIMEOUT=120 HF_HUB_ETAG_TIMEOUT=60 \
  data/.venv/bin/python -m tools.amazon.download_amazon_metadata
```

若网络环境对 Xet 传输不兼容，可尝试官方支持的 HTTP 下载回退：

```bash
HF_HUB_DISABLE_XET=1 data/.venv/bin/python -m tools.amazon.download_amazon_metadata
```

查看全部参数：

```bash
data/.venv/bin/python -m tools.amazon.download_amazon_metadata --help
```

## 数据来源和使用条件

- [Amazon Reviews 2023 项目主页](https://amazon-reviews-2023.github.io/)
- [Hugging Face 商品元数据文件列表](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/tree/main/raw/meta_categories)
- [Hugging Face 官方下载说明](https://huggingface.co/docs/huggingface_hub/guides/download)

数据与脚本的使用条件不同。研究、公开展示、分发或商业使用前，请核对源数据集页面的使用条件、许可和引用要求。下载工具不扩大原数据的使用授权。

## 下载完成后录入数据库

参见 [数据库导入说明](IMPORT.md)。导入脚本使用项目应用环境 `.venv`，支持预览、进度条、分批事务、稳定去重和断点续跑，并明确区分真实商品资料与模拟经营数据。
