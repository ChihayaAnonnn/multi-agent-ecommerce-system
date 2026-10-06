# 将 Amazon 商品资料导入本机数据库

`tools/amazon/import_amazon_metadata.py` 将下载的真实商品资料写入现有目录表，为每个商品族建立一个明确标记的模拟默认 SKU，并生成可复现的测试人民币价格和库存。数据适合本地购物与 Agent 测试，不代表真实商家的售价、库存或完整 SKU。

## 使用环境

**使用项目的 `.venv`，不是下载工具的 `data/.venv`。** 导入需要项目已有的 SQLAlchemy、asyncmy、配置和领域模块，额外依赖只有进度条。

```bash
cd /Users/vivix/Desktop/workspace/multi-agent-ecommerce-system

# 若项目虚拟环境没有 pip，先执行这一条
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r tools/amazon/import-requirements.txt
```

也可以用项目已有的 uv 安装：

```bash
uv pip install --python .venv/bin/python -r tools/amazon/import-requirements.txt
```

新建项目环境还需先安装根目录 `requirements.txt`。数据库服务器、用户、密码取自项目 `.env` 的 `ECOM_DATABASE_URL`；脚本不会打印密码。只允许本机 `mysql+asyncmy` 连接，实际写入必须明确指定库名。

## 先预览，再导入

预览六类共 60 条有效记录（每类 10 条），不连接数据库、不写断点：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata --dry-run --limit 60
```

导入当前页面使用的本机演示库：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata \
  --database-name commerce_mvp_demo_20260930
```

默认处理最多 **100,000 条有效源记录**，均分到六个品类。跨品类重复商品按 `parent_asin` 合并，最终新增商品数可能少于 100,000。采样取每个文件前面的有效记录，稳定、适合开发；它不是统计意义上的随机抽样，也不保证文件后部商品的覆盖。

每批默认 500 条，流式读取，内存不会随全量文件大小增长。每个品类有终端进度条，显示处理速度、累计写入和异常记录数。首次任务会创建六个独立的 Amazon 来源类目及其属性定义，保留已有演示商品。

**当前项目配置库 `ecommerce` 与页面演示库不同。** 若导入前者，当前 18443 演示页面不会自动切换到前者。脚本不创建、不删除数据库，也不自动迁移；目标库必须已有目录、价格、库存表和默认库位。迁移仍使用项目原有 Alembic 流程。

## 扩大范围或全量

扩展到一百万条有效源记录：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata \
  --database-name commerce_mvp_demo_20260930 --limit 1000000
```

全量扫描六类约 906 万条源记录：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata \
  --database-name commerce_mvp_demo_20260930 --limit 0
```

使用同一目标和配置时，增加 `--limit` 会接着原断点向后处理，不重读已确认的部分。全量模式显示按文件字节推进的进度条。26.96 GB 原文件大小不等于数据库占用；索引、JSON、价格/库存流水、事务日志还会占空间。十万条导入后，先观察数据库体积和耗时再决定是否全量。

只选部分品类：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata \
  --database-name ecommerce \
  --categories Pet_Supplies Grocery_and_Gourmet_Food \
  --limit 20000
```

改变类别集合须指定新的 `--checkpoint` 路径，因为旧断点记录了原类别集合。新路径仍会按稳定 ID 跳过已有商品，不覆盖其经营数据。默认断点都位于忽略目录 `data/import_state/`；自定义断点放在该目录也会被 Git 忽略。

## 真实资料与模拟数据

| 字段 | 处理方式 |
| --- | --- |
| 商品 ID | 根据 `parent_asin` 生成稳定 UUID 和 `AMZ-` 编码，跨品类合并 |
| 名称 | 保留原英文，展示字段最长 255 字符，完整原名称保存在来源属性 |
| 品牌 | 仅提取明确的 `Brand` / `Brand Name`，不把商店名称猜成品牌 |
| 描述、特征 | 合并文本，按 MySQL TEXT 字节限制截断，完整内容仍在原文件 |
| 类目 | 六个中文根类目，原始英文类目路径另存为来源属性 |
| 原属性 | `source_details` 保存 JSON 文本，可回查原资料；不编造缺失性能属性 |
| 图片 | 最多保留一条可用 HTTPS 图片 URL，不下载图片文件 |
| 评分、评价数 | 保留存在且有限的非负数，缺失不补造 |
| 历史美元价格 | 单独保留为 `source_price_usd`，不视为当前价格或汇率换算结果 |
| 当前测试售价 | 在类目测试区间内按固定种子生成整数分，写入 CNY 现价及初始化流水 |
| SKU | 一个无选项的模拟默认 SKU，不从颜色文本推断真实可售组合 |
| 库存 | 明确标记的模拟余额；未知库存不创建余额记录 |

来源属性包含 `source_parent_asin`、`source_revision`、`simulation_seed`、`simulation_version`、`simulation_price=true`、`simulation_sku=true` 和初始库存状态。品牌、尺寸等缺失事实不会由 LLM 生成。

### 库存规则

稳定哈希分别给库存状态、数量和价格取种子，不受输入顺序影响。默认全局种子 `42`、规则版本 `amazon-demo-v1`。

| 初始状态 | 目标概率 | 数量 |
| --- | --- | --- |
| 正常 | 80% | 按类目取区间，见下表 |
| 少量 | 10% | 1–5 |
| 缺货 | 8% | 0，明确有余额记录 |
| 未知 | 2% | 无余额记录，与缺货语义不同 |

有限样本比例可能偏离目标概率。`reserved` 始终初始化为 0，不模拟当前系统尚未开放的库存预留。

| 品类 | 正常库存区间 | 测试价格区间（元） |
| --- | --- | --- |
| 数码电子 | 6–30 | 20–8,000 |
| 家居厨房 | 6–30 | 10–3,000 |
| 美妆个护 | 10–80 | 10–1,500 |
| 运动户外 | 6–40 | 20–5,000 |
| 宠物用品 | 10–80 | 10–1,500 |
| 食品杂货 | 20–100 | 5–500 |

价格在对数区间上分布，使测试低价、中价商品多于高价商品。这些区间是人工测试配置，没有真实市场统计依据；对具体子类目的拟合和价格校准留待后续。宽类目的商品不能仅靠模拟价格判断真实性价比。

## 断点、事务和重复运行

- `Ctrl+C` 后重跑原命令即可继续。每批商品、SKU、媒体、价格及价格流水、库存及库存流水在同一个事务提交，成功后才原子保存断点。
- 已存在商品直接跳过，不重置后续调整过的价格、库存、商品状态或媒体。数据库提交后、断点保存前中断，也不会重复新增。
- 同库使用 MySQL 命名锁，阻止两个导入进程同时运行。不要绕过脚本在导入期间并行修改来源类目结构。
- 默认断点按目标服务器和库名生成独立文件，记录源文件大小/修改时间、数据版本、种子、品类、字节偏移和统计，不含数据库密码。
- 增加处理上限或改变批量大小可以续跑；更换源文件、种子、品类或目标后，旧断点会拒绝复用。改变种子不会强行刷新既有商品，需要独立测试库生成另一套场景。
- `inserted` 是断点记录的累计新增数量；提交后断点丢失的窗口可能少计，因此结果另外查询数据库实际商品数 `database_products`。`valid` 与库存状态统计按有效源记录累计，包含跨品类重复记录。
- 无效 JSON、无效 `parent_asin`、空名称等记录跳过并统计；数据库或磁盘错误会停止，未提交批次回滚。原文件保留，可用于后续排查。

`--dry-run` 不读取写入断点，总是从源文件开头预览，`inserted=0`，也不提供数据库实际计数。

## 如何验证导入结果

脚本完成后查看 `database_products`，这是所选来源类目、种子与规则对应的数据库商品数。也可在数据库客户端只读查询：

```sql
SELECT COUNT(*) FROM products WHERE product_code LIKE 'AMZ-%';
SELECT category_id, COUNT(*) FROM products
WHERE product_code LIKE 'AMZ-%' GROUP BY category_id;
```

导入不会自动建立 Milvus 向量索引，不翻译标题，不扩展 Agent 的自然语言理解。现有目录的关键词搜索可以使用这些商品，但数据量增大后的检索效果、受控扫描上限和召回能力仍需独立验证。

“确认前售罄”等动态场景应在测试中通过 `InventoryService.adjust()` 调整指定 SKU，并断言最终业务结果；不要每次导入随机改动已有库存，也不要直接修改余额表。库存工具故障应通过测试替身注入，不通过删除数据库表实现。

查看全部选项：

```bash
.venv/bin/python -m tools.amazon.import_amazon_metadata --help
```

## 本次验证（2026-10-01）

全套 Python 测试 175 项通过，其中导入相关 8 项覆盖稳定生成、去重、保留经营调整、未知库存、批量回滚、断点扩容、提交后断点失败重放及同库互斥。实际源文件六类共 60 条记录在独立临时 MySQL 库完成命令行导入，重复执行后商品数保持 60。测试库已清理，演示库仍为 8 个原有商品、0 个 Amazon 导入商品；大批量导入由你执行上述命令启动。
