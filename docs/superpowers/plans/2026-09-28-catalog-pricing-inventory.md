# 商品目录、价格与库存实体实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本仓建立平台自营、SKU 级商品目录、价格与库存的可信 MySQL 模型及查询接口，供后续 Agent 和购物车使用。

**Architecture:** 保持一个 FastAPI 部署，新增 `catalog/`、`pricing/`、`inventory/` 三个领域模块。每个模块拥有自己的 SQLAlchemy 表、服务和 DTO；库存与价格写入在各自事务内完成。旧推荐接口维持响应形状，增加显式适配器，不在本阶段接入支付、购物车或账号系统。

**Tech Stack:** Python、FastAPI、Pydantic v2、SQLAlchemy 2.x async、asyncmy、MySQL 8.0、Alembic、pytest。MySQL 由 `compose.infra.yml` 提供；数据库迁移在独立测试库验证。

**Spec:** `docs/superpowers/specs/2026-09-28-catalog-pricing-inventory-design.md`

**Status:** 已实施；原计划清单未同步勾选。`0001`–`0003` 迁移、`catalog/`、`pricing/`、`inventory/` 与只读 API 已落地；2026-09-30 专项回归 `45 passed`、零跳过。MVP 新增的批量事实和属性硬过滤见 `2026-09-30-commerce-agent-mvp.md` 任务 1。

## Global Constraints

- 首版平台自营，目录和库存由本项目维护；支付由另一项目提供，本计划不建支付、订单、购物车和用户账号表。
- 首版只支持按整数件销售的实物商品、CNY 价格和至少一个默认库位。
- `Product` 负责展示，`SKU` 为可售单元；商品表不得持久化当前价格或库存，Agent 不直接写业务表。
- MySQL 8.0、InnoDB、`utf8mb4`、UTC 时间；实体 ID 为应用生成的 UUID 字符串 `CHAR(36)`，关联表允许复合主键。
- 金额以整数分 `BIGINT` 存储，拒绝浮点输入；目录/价格/库存的异常状态均不得伪装成可售。
- 数据库迁移使用 Alembic；不在应用启动时隐式建表。现有 `POST /api/v1/recommend` 与 graph 响应形状保持兼容。

## Review Focus

以下输入与故障容易漏测，已分别落入对应任务的测试：

1. `option_id` 与 `value_id` 分属不同商品或选项时，创建 SKU 必须失败（任务 2）。
2. 对已上架商品追加选项时，旧 SKU 会变成不完整组合；目录服务必须拒绝该变更，直到商品转为草稿（任务 2）。
3. 两个价格写入持有相同旧版本时，只有一个成功，历史只出现一次新版本（任务 4）。
4. 相同库存幂等键携带不同 SKU 或调整量时，必须报冲突，不能返回另一请求的结果（任务 5）。
5. 无库存行、价格缺失和数据库查询失败须有不同输出，不能被 Agent 当作有货有价（任务 6）。

---

## 文件与职责

| 路径 | 职责 |
| --- | --- |
| `alembic.ini`、`migrations/env.py`、`migrations/versions/*.py` | 数据库迁移；每个领域任务新增自己的迁移版本。 |
| `db/base.py`、`db/session.py` | 共享 ORM 基类与异步会话工厂，仅提供基础设施。 |
| `catalog/models.py`、`catalog/schemas.py`、`catalog/service.py` | 目录表、输入输出类型、商品与 SKU 规则。 |
| `pricing/models.py`、`pricing/schemas.py`、`pricing/service.py` | 当前价格、变更记录、版本更新。 |
| `inventory/models.py`、`inventory/schemas.py`、`inventory/service.py` | 库位余额、库存流水、原子调整和可售查询。 |
| `api/catalog.py`、`api/commerce_read.py` | 消费者只读商品、价格、可售查询；写入仅通过受控领域命令。 |
| `catalog/legacy_adapter.py` | 从新目录 DTO 显式生成旧 `models.schemas.Product`，不把旧样例作为事实源。 |
| `tests/conftest.py`、`tests/test_*_mysql.py` | 使用独立 MySQL 测试库验证迁移、约束和事务；无测试库时给出明确跳过原因。 |

## Task 1：MySQL 测试基线与目录表

**Files:** Create `db/base.py`, `db/session.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_catalog.py`, `catalog/models.py`, `tests/conftest.py`, `tests/test_catalog_mysql.py`; modify `requirements.txt`, `.env.example`, `config/settings.py`.

**Interfaces:** `db.session.create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]`；`catalog.models` 导出 `Category`, `CategoryAttribute`, `Product`, `ProductOption`, `OptionValue`, `SKU`, `SKUOptionValue`, `ProductMedia`。后续任务只经会话工厂与 ORM 类型使用本任务成果。

- [ ] **Step 1: Write failing MySQL schema test.** `test_catalog_migration_constraints` 升级空测试库后检查 8 张目录表、`products.product_code` 唯一、`skus.sku_code` 唯一、`skus(product_id, option_signature)` 唯一，以及 `products` 没有 `price`/`stock` 列。测试库 URL 由 `ECOM_TEST_DATABASE_URL` 显式提供；拒绝与 `ECOM_DATABASE_URL` 相同的库名。运行本任务前必须配置隔离 MySQL 测试库，跳过不算完成。
- [ ] **Step 2: Run red test.** `python -m pytest tests/test_catalog_mysql.py::test_catalog_migration_constraints -q`；应因迁移或表不存在而失败。
- [ ] **Step 3: Implement foundation.** 加入 Alembic 依赖；配置异步迁移和 MySQL 连接；按设计稿第 4 节建立 8 张目录表及索引/FK；`ECOM_DATABASE_URL` 示例改为 `mysql+asyncmy://...` 占位值，`Settings.database_url` 默认设为空字符串，仅在创建数据库会话时校验为 MySQL URL。不要自动运行迁移，也不要使旧推荐 API 因未配置数据库而无法启动。
- [ ] **Step 4: Run green checks.** 在独立 MySQL 测试库运行 `python -m pytest tests/test_catalog_mysql.py -q`，再运行 `python -m pytest tests/ -q`；两者均须通过。验证迁移可从空库升级，也能降级回基线。
- [ ] **Step 5: Commit.** `git add db catalog migrations alembic.ini requirements.txt .env.example config/settings.py tests && git commit -m "feat: establish catalog schema and migrations"`。

## Task 2：目录写入与 SKU 组合校验

**Files:** Create `catalog/schemas.py`, `catalog/service.py`; modify `tests/test_catalog_mysql.py`.

**Interfaces:** `CatalogService(session_factory, price_reader: PriceReader | None = None)` 提供 `create_category(data: CategoryInput) -> CategoryView`、`add_category_attribute(category_id: str, data: AttributeInput) -> AttributeView`、`create_product(data: ProductInput) -> ProductView`、`add_option(product_id: str, data: OptionInput) -> OptionView`、`add_option_value(option_id: str, data: OptionValueInput) -> OptionValueView`、`add_media(product_id: str, data: MediaInput) -> MediaView`、`create_sku(product_id: str, data: SKUInput) -> SKUView`、`activate_product(product_id: str, expected_version: int) -> ProductView`、`archive_product(product_id: str, expected_version: int) -> ProductView`。`PriceReader.has_current_prices(sku_ids: list[str]) -> bool` 是只读协议，任务 2 测试使用桩，任务 4 注入真实价格服务；没有价格读取器时拒绝上架。输入 DTO 均在 `catalog/schemas.py`；每个写入方法自管一个事务。

- [ ] **Step 1: Write failing behavior tests.** 覆盖无选项默认 SKU、仅实际创建的组合存在、重复编码/组合失败、选项和值跨商品或跨选项失败、上架缺 SKU/缺价失败、上架/归档版本冲突、归档后不物理删除、父类目循环失败、类目属性类型不匹配、媒体绑定其他 Product 的 SKU 失败；对已上架商品追加选项应拒绝。
- [ ] **Step 2: Run red tests.** `python -m pytest tests/test_catalog_mysql.py -q`；新用例须失败于缺少领域方法或规则。
- [ ] **Step 3: Implement domain writes.** SKU 签名按选项 ID 排序、固定编码后计算 SHA-256；无选项为 `default`。校验外键归属与选项完整性；Product 仅草稿可变更规格结构；上架时通过价格查询边界确认每个 active SKU 有 CNY 价格。使用数据库唯一键处理竞态，转换为明确的领域冲突。
- [ ] **Step 4: Run green tests.** `python -m pytest tests/test_catalog_mysql.py -q` 通过；`python -m pytest tests/ -q` 无回归。
- [ ] **Step 5: Commit.** `git add catalog tests && git commit -m "feat: validate product and sku writes"`。

## Task 3：目录读取和搜索

**Files:** Modify `catalog/service.py`, `catalog/schemas.py`, `tests/test_catalog_mysql.py`.

**Interfaces:** `CatalogService.get_product(product_id: str) -> ProductView`、`get_sku(sku_id: str) -> SKUView`、`search(category_id: str | None, text: str | None, limit: int, cursor: str | None) -> ProductPage`。`ProductPage` 返回 `items` 与 `next_cursor`；游标由稳定 `(created_at, id)` 元组编码。

- [ ] **Step 1: Write failing read tests.** 搜索仅返回 active 商品，按稳定游标翻页且不重不漏；返回真实 SKU 组合、媒体和类目属性；停用 SKU/归档商品状态可在单项读取中辨别；超大 `limit`、无效游标和不存在的类目均有确定错误。
- [ ] **Step 2: Run red tests.** `python -m pytest tests/test_catalog_mysql.py -q`；新读取用例失败。
- [ ] **Step 3: Implement reads.** 查询避免按每个商品逐条加载 SKU；`limit` 限制为 1–100，文本仅匹配名称/商品编码，游标采用含时间和 ID 的不透明编码，不接受任意 SQL 排序字段。
- [ ] **Step 4: Run green tests.** `python -m pytest tests/test_catalog_mysql.py -q` 通过。
- [ ] **Step 5: Commit.** `git add catalog tests && git commit -m "feat: query catalog and sku details"`。

## Task 4：SKU 价格与版本历史

**Files:** Create `pricing/models.py`, `pricing/schemas.py`, `pricing/service.py`, `migrations/versions/0002_pricing.py`, `tests/test_pricing_mysql.py`; modify `catalog/service.py` 的上架价格边界。

**Interfaces:** `PricingService(session_factory).get_prices(sku_ids: list[str]) -> dict[str, PriceView]`、`has_current_prices(sku_ids: list[str]) -> bool`、`set_price(sku_id: str, amount_minor: int, expected_version: int | None, reason: str) -> PriceView`。首次写价时 `expected_version=None`，之后必须匹配现行版本；无价 SKU 不出现在结果映射里。目录上架通过注入的 `has_current_prices` 校验。

- [ ] **Step 1: Write failing price tests.** 1 元等于 `100` 分；拒绝 `float`、负数和非 CNY；缺价不回填旧价；版本不匹配不写历史；相同旧版本并发更新只有一个成功，历史版本唯一；无效 SKU 不写价。
- [ ] **Step 2: Run red tests.** `python -m pytest tests/test_pricing_mysql.py -q`；迁移/方法缺失使新用例失败。
- [ ] **Step 3: Implement price model and service.** 建当前价与追加式价格变更表；一个事务中按版本更新价格并写历史；保持目录与价格独立，通过只读接口协作。
- [ ] **Step 4: Run green checks.** `python -m pytest tests/test_pricing_mysql.py tests/test_catalog_mysql.py -q` 通过；迁移从 `0001` 升至 `0002` 并降回 `0001` 成功。
- [ ] **Step 5: Commit.** `git add pricing catalog migrations tests && git commit -m "feat: manage versioned sku prices"`。

## Task 5：库存余额、流水与原子调整

**Files:** Create `inventory/models.py`, `inventory/schemas.py`, `inventory/service.py`, `migrations/versions/0003_inventory.py`, `tests/test_inventory_mysql.py`.

**Interfaces:** `InventoryService(session_factory).get_availability(sku_ids: list[str]) -> dict[str, AvailabilityView]`；`adjust(sku_id: str, location_id: str, delta: int, idempotency_key: str, reason: str) -> BalanceView`。`AvailabilityView.status` 为 `available/out_of_stock/unknown`，包含数量、版本、读取时间；没有余额行返回 `unknown`，数据库故障抛出依赖错误，不能转为有货。库存首次建档通过 `create_balance(sku_id: str, location_id: str) -> BalanceView`，初始 `on_hand=reserved=0`。创建默认库位通过受控管理命令或迁移种子完成。

- [ ] **Step 1: Write failing inventory tests.** 从零余额调整入库；不足时出库拒绝且无流水；并发出库总量不能超卖；同幂等键同载荷只记一次；同键不同载荷报冲突；无余额行为 `unknown`，零余额为 `out_of_stock`；多库位数量正确汇总。
- [ ] **Step 2: Run red tests.** `python -m pytest tests/test_inventory_mysql.py -q`；迁移/方法缺失使新用例失败。
- [ ] **Step 3: Implement inventory transaction.** 建库位、余额与流水；锁定 `(sku_id, location_id)` 余额行，校验 `0 <= reserved <= on_hand`，更新版本和流水同事务提交；幂等键按请求字段核对，冲突不改余额。第一阶段不提供预留写入。
- [ ] **Step 4: Run green checks.** `python -m pytest tests/test_inventory_mysql.py -q` 通过；迁移从 `0002` 升至 `0003` 并降回 `0002` 成功。
- [ ] **Step 5: Commit.** `git add inventory migrations tests && git commit -m "feat: track sku inventory atomically"`。

## Task 6：消费者只读契约与旧模型适配

**Files:** Create `api/catalog.py`, `api/commerce_read.py`, `catalog/legacy_adapter.py`, `tests/test_commerce_read_api.py`; modify `main.py`, `api/runtime.py`, `tests/test_api_routes.py`.

**Interfaces:** `GET /api/v1/catalog/products`、`GET /api/v1/catalog/products/{product_id}`、`GET /api/v1/catalog/skus/{sku_id}`、`GET /api/v1/commerce/skus/{sku_id}/quote`。Quote 返回 SKU、价格及版本、可售状态/数量及版本、读取时间；缺价或库存未知时 `purchasable=false` 且原因明确。`catalog.legacy_adapter.to_legacy_product(product, price, availability) -> models.schemas.Product` 仅为旧响应构造兼容视图。

- [ ] **Step 1: Write failing API tests.** 商品列表与详情返回 Product/SKU 分层；报价 `amount_minor` 精确；缺价、零库存、未知库存和后端查询异常返回不同状态且均不可买，依赖故障返回 HTTP 503；不存在的 SKU 为 404；旧推荐路由响应形状与当前测试保持一致；显式适配器把 1299 分转成旧模型展示价 12.99。
- [ ] **Step 2: Run red tests.** `python -m pytest tests/test_commerce_read_api.py -q`；新路由缺失导致失败。
- [ ] **Step 3: Implement read composition.** 通过 `api/runtime.py` 在有效 MySQL 配置下初始化三领域查询服务；未配置时旧推荐 API 仍能启动，新只读端点返回明确的 503。API 只拼装领域 DTO，不直连 ORM。适配器将整数分转换为旧 `price` 浮点仅用于旧接口展示，并标记旧接口为过渡契约；新接口全程使用整数分。旧 Agent 仍为样例流程，本阶段不把样例库存写入真实表。
- [ ] **Step 4: Run green checks.** `python -m pytest tests/test_commerce_read_api.py tests/test_api_routes.py -q` 与 `python -m pytest tests/ -q` 均通过；在隔离 MySQL 测试库复跑三组数据库测试。
- [ ] **Step 5: Commit.** `git add api catalog main.py tests && git commit -m "feat: expose sku catalog and availability reads"`。

## 完成门槛

独立 MySQL 测试库中升级全部迁移、运行 `python -m pytest tests/ -q`、核对 `git diff --check` 与工作区状态。人工查看响应中商品族与 SKU 的区别、缺价/未知库存的不可购买状态，以及旧推荐接口是否仍明确是样例数据。缺少数据库环境时不能声称数据库约束、并发或迁移已验证完成。
