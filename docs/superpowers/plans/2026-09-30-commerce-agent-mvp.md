# 电商 Agent MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在已完成的身份、商品目录、价格和库存基础上，实现“登录 → 表达需求 → 发现与比较真实 SKU → 明确确认改车 → 重新核验 → 宿主结账交接”的内部演示闭环。

**Architecture:** 沿用一个 FastAPI 部署和 MySQL，以 `principal_key` 统一购物资源归属；`shopping_session/` 保存会话、轮次及租约，独立的 LangGraph commerce 图按轮组织事实与提案，`cart/` 在同一数据库事务中执行确认，`checkout/` 保存不可变交接快照并调用受控网关。旧推荐接口保持原契约；生产环境没有真实宿主接口时明确返回 `checkout_unavailable`，绝不生成模拟支付入口。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.x async + asyncmy、MySQL 8、Alembic、现有 `identity/`、现有 LangGraph `StateGraph` 与 LangChain/OpenAI 兼容客户端、pytest/httpx；不新增购物车微服务、支付 SDK、Agent 框架或向量检索依赖。

**Spec:** `docs/superpowers/specs/2026-09-29-commerce-agent-mvp-design.md`（原 MVP 范围及 2026-09-30 编排修订均已审阅通过）。身份细节见 `docs/superpowers/specs/2026-09-29-identity-module-design.md`。

## 前置能力核验与执行顺序

| 前置项 | 当前证据 | 本计划处理 |
| --- | --- | --- |
| 商品目录、价格、库存 | `catalog/`、`pricing/`、`inventory/`、迁移 `0001`–`0003`、`api/commerce_read.py` 已落地；对应 45 个专项测试于 2026-09-30 通过、0 跳过 | 不重新执行 `2026-09-28-catalog-pricing-inventory.md`；仅补本 MVP 所需批量读取、硬过滤和事务内窄接口。旧计划标题、状态和未勾选步骤不能覆盖实际代码与测试证据。 |
| 演示身份 | `identity/`、迁移 `0004`、`api/identity.py:require_principal` 已实现并验收 | 直接复用；交易路由全部依赖真实 Bearer 主体，测试替身只在测试应用注入。 |
| 购物会话、真实选品、购物车、交接 | 当前无对应业务模块、表或路由 | 按任务 1–8 实施；每项经独立测试后再开放下一层写入口。 |

本计划是一个强依赖的纵向交付链：事实查询 → 会话 → 发现 → 购物车 → 确认 → 交接 → 独立 commerce 图与模型 → 页面验收。若只需先展示真实选品，可在任务 3 后交付只读演示，但不能称为完整 MVP；任务 7 前的 `/turns` 仅是确定性实现，任务 7 后才按设计 §5.4.1 经图编排。旧商品基础计划无需先重做；其回归测试是本计划每次全量验证的一部分。

## 技术选择与取舍

| 判断 | 采用及原因 | 代价、边界 |
| --- | --- | --- |
| 购物车与身份同仓、同 MySQL | 复用已验证的主体、SKU 和事务，跨域确认可一次提交 | 写入耦合单库；未来拆服务需重新设计一致性，当前不拆微服务。 |
| 属性过滤先用稳定游标的受控分页扫描 + 批量详情 | 当前 `CatalogService.search` 只有类目/文本筛选；MVP 不新增复杂 JSON 索引与动态 SQL | 达扫描上限返回 `incomplete=true`，不能声称穷尽匹配；后续有量级证据再加专用索引。 |
| 硬条件、报价和确认由代码决定，LLM 只解析/排序/解释 | 防止模型造商品、猜库存或绕过确认 | 需要较多确定性 DTO 和测试；模型失败时退回确定性结果。 |
| 独立 LangGraph `StateGraph` 按轮编排 | 本仓已有依赖，明确路由商品发现、比较、读车和提案；以后推荐算法和客服图可分别演进 | 图节点与租约、提案幂等需要显式接口；MVP 不引入 checkpointer/`interrupt()`，MySQL 仍是购物状态事实源。 |
| 暂不切换 CrewAI、AgentScope、AutoGen/Agent Framework 或通用 `create_agent` | 当前交易路径要求代码控制，现有 LangGraph/LangChain 可满足；减少新运行时与模型工具调用的不确定性 | 将来出现独立 Agent 团队或复杂自治协作时，按相同领域 DTO 做原型和时延/维护评估；框架取舍详见设计 §2.1。 |
| 行锁 + 唯一键 + 稳定幂等键 | 多进程和多端同时确认时可验证一次写入 | 事务内只读数据库，不持锁调用 LLM/宿主网络；锁顺序和重试冲突必须测试。 |
| 宿主网关 Protocol + 内部 `demo` 实现 | 宿主结账契约尚未落实，可先验收交接状态机 | 演示交接不等于订单或支付；生产未配置可信宿主时 503，不能直接调用现有 Billing 单行 Stripe 接口。 |

## Global Constraints

- 新交易路由使用 `Depends(require_principal)`；禁止读取请求体 `user_id` 或启用固定生产主体。跨用户会话、车、动作、交接均返回 404；本人过期会话返回 `session_expired`。
- 仅平台自营、已登录用户、实际存在的 SKU、CNY 整数分；不建订单、支付、库存预留、优惠、运费、税费、匿名车或登录合并。
- 会话建议 24 小时、提案建议 10 分钟；在 `config/settings.py` 配置 `commerce_session_ttl_seconds=86400`、`commerce_action_ttl_seconds=600`。内部演示的 `commerce_max_line_quantity=10` 只作可配置保护值，正式限购政策须产品确认。短名单最多 3 项；受控扫描上限 `commerce_scan_limit=200`，未穷尽时必须标 `incomplete`。
- 迁移接续现有 `0004_identity`；新增 ID 用 UUID `CHAR(36)`，`principal_key` 使用现有 `identity.types.principal_key_for` 的 64 位十六进制 SHA-256 结果，时间用 UTC `DATETIME(6)`，金额用整数分。只在 Alembic 建表，不在应用启动自动建表。
- `POST /turns` 同 `session_id + turn_id + request_hash` 回放持久结果；同 ID 异参 409。确认用稳定 `idempotency_key`，同键同参只写一次，同键异参 409；数据库提交后才宣称写入成功。
- 同会话一次只允许一个有效 `TurnClaim` 租约；图节点的提案/预览须带当前租约，并由 `(session_id, source_turn_id)` 唯一键防止过期恢复重复创建动作。旧租约不能提交响应或提案。
- 新 commerce 图只执行异步受控节点和提案/预览服务，不直接读写 ORM、调用确认命令或触发宿主交接；聊天确认在图外经确定性门禁处理。图 State 单轮有效，跨轮条件、已见来源和结果存 MySQL，不启用 LangGraph checkpointer/`interrupt()`。
- 交易路由只从真实目录、价格和库存得出可买结论；缺价、库存未知、下架或依赖故障一律不可买，不回填旧 `MOCK_PRODUCTS`。旧 `/api/v1/recommend` 和 `/graph` 保持响应形状。
- `api/commerce.py` 统一映射设计 §9 的错误码：缺凭证 401、他人资源 404、版本/幂等冲突 409、无效输入 422、可信依赖或宿主不可用 503；不能把依赖故障翻译成空购物车或可购买状态。
- 测试使用隔离 `ECOM_TEST_DATABASE_URL`，MySQL 用例跳过不算验收；LLM 与宿主用受控替身。真实宿主签约、服务鉴权、重校价/库存及幂等回读未落实前，只能验收内部演示闭环。

## Review Focus

1. 另一个用户拿到 `session_id`、`cart_id`、`action_id` 或 `handoff_id` 后仍只能得到 404；任务 2、5、6 的 HTTP 与服务测试覆盖。
2. 同一 `turn_id` 的并发重试、租约过期恢复及另一个轮次接管，不能产生第二个提案或让迟到结果覆盖新响应；任务 2、5、6、7 的租约与来源唯一键测试覆盖。
3. 硬属性筛选扫描达到上限时必须报告 `incomplete=true`，且不以错类目、超预算或无价 SKU 凑满短名单；任务 1、3 覆盖。
4. 用户所见价格、规格或可售量在确认前变化时旧动作失效，不能写车或交接；聊天确认必须绕开图和模型复用按钮命令。任务 5、6、7 覆盖，并区分只变库存内部版本的情况。
5. 交接提交后宿主超时，重复确认必须恢复同一 `handoff_id`，不生成第二个目的地或宣称支付成功；任务 6、8 覆盖。

## 文件与职责

| 文件组 | 职责 |
| --- | --- |
| `migrations/versions/0005_commerce_sessions.py`、`shopping_session/models.py`、`shopping_session/service.py`、`shopping_session/schemas.py` | 会话、已见商品/SKU、轮次声明与归属；持久约束。 |
| `catalog/service.py`、`pricing/service.py`、`inventory/service.py`、`commerce_orchestrator/facts.py` | 批量事实读取及共享事务中的受控锁定查询；各领域仍只写自己的表。 |
| `commerce_orchestrator/intent.py`、`discovery.py`、`compare.py`、`service.py`、`model.py`、`schemas.py` | 结构化意图、硬筛选、可信排序/解释、单轮图状态与轮次入口；不直接执行车写入。 |
| `commerce_orchestrator/graph.py`、`nodes.py` | 独立 `StateGraph` 的构建、条件边和异步节点；依赖经构造注入，节点只访问受控领域服务。 |
| `migrations/versions/0006_cart.py`、`cart/models.py`、`cart/schemas.py`、`cart/service.py` | 按主体唯一的车、行与待确认动作；版本、幂等、事务确认。 |
| `migrations/versions/0007_checkout.py`、`checkout/models.py`、`checkout/schemas.py`、`checkout/service.py`、`checkout/gateway.py` | 交接预览、不可变快照、恢复状态机、受控宿主网关。 |
| `migrations/versions/0008_commerce_events.py`、`commerce_events/models.py`、`commerce_events/service.py` | 去重的页面事件；采集失败不影响购物事务。 |
| `models/commerce.py`、`api/commerce.py`、`api/runtime.py`、`main.py`、`config/settings.py`、`.env.example` | 新 HTTP 契约、依赖装配、明确错误映射、演示开关与配置占位符。 |
| `demo/commerce.html`、`demo/commerce.js`、`api/commerce_demo.py` | 仅开发/演示环境可用的同源 HTTPS 验收页；正式宿主页面另按相同契约实现。 |

## Task 1：可信批量事实与受控目录扫描

**Files:** 修改 `catalog/service.py`、`pricing/service.py`、`inventory/service.py`；新增 `commerce_orchestrator/facts.py`、`commerce_orchestrator/schemas.py`、`tests/test_commerce_facts.py`、`tests/test_commerce_facts_mysql.py`。

**Interfaces:** `CatalogService.get_products(product_ids: list[str]) -> dict[str, ProductDetailView]` 与 `get_skus(sku_ids: list[str]) -> dict[str, SKUDetailView]` 批量读取；`CommerceFactsReader.quote_many(sku_ids: list[str]) -> dict[str, SKUQuote]` 返回 SKU、Product 状态、CNY 现价/版本、可售量/时间、明确原因。`scan(intent: ShoppingIntent, max_scanned: int, page_size: int = 20) -> CandidatePage` 复用稳定游标，返回匹配候选及 `incomplete`；`ShoppingIntent` 在本任务的 `commerce_orchestrator/schemas.py` 定义，字段及来源按设计 §5.1。

- [x] **Step 1: Write failing tests.** `test_quote_many_never_promotes_unknown_to_purchasable` 用两类真实 SKU 断言活跃、有价、有货可买，而草稿/归档、无价、零库存、未知库存不可买；价格/库存故障抛依赖错误。`test_scan_hard_filters_and_limit` 断言类目、属性、预算按 SKU 过滤，受控扫描达到上限时 `incomplete=true` 且不补错候选；批量读取的 SQL 次数不随 SKU 数逐个增长。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_commerce_facts.py tests/test_commerce_facts_mysql.py -q`；预期缺少批量接口或 DTO 而失败，MySQL 测试不得跳过。
- [x] **Step 3: Implement.** 批量详情复用现有 `_load_*` 辅助方法；`scan` 只使用 `CatalogService.search` 的固定游标与实际目录属性，对 `is_filterable` 的明示条件作代码硬过滤。报价一次批量查询价格、一次批量查询库存；查询失败不转换为空映射。此任务不在目录新增属性 JSON 索引，扫描上限由配置传入。
- [x] **Step 4: Run green checks.** 两个新测试文件及 `tests/test_catalog_mysql.py tests/test_pricing_mysql.py tests/test_inventory_mysql.py tests/test_commerce_read_api.py` 全部通过；记录实际数量与零跳过。
- [x] **Step 5: Commit.** 只暂存本任务文件，提交 `feat: add trusted batch commerce facts`。

## Task 2：购物会话、已见来源与归属

**Files:** 新增 `0005_commerce_sessions.py`、`shopping_session/{models,schemas,service}.py`、`api/commerce.py`、`models/commerce.py`、`tests/test_shopping_session_mysql.py`、`tests/test_commerce_auth_api.py`；修改 `main.py`、`api/runtime.py`、`config/settings.py`、`.env.example`。

**Interfaces:** `ShoppingSessionService.create(principal: Principal) -> ShoppingSessionView`；`get_owned(session_id: str, principal: Principal) -> ShoppingSessionView`；`record_seen(session_id: str, principal: Principal, product_ids: list[str], sku_ids: list[str]) -> None` 仅由业务服务传入已生成的响应 ID，不暴露为任意客户端写接口；`claim_turn(session_id, principal, turn_id, request_hash) -> TurnClaim | TurnReplay`，其中 `TurnClaim` 含 `lease_token` 和 `session_version`；`assert_current_turn(db: AsyncSession, claim: TurnClaim) -> None`；`complete_turn(claim: TurnClaim, completion: TurnCompletion) -> None`。`TurnCompletion` 含校验后的响应、更新后的购物状态及实际展示的 Product/SKU ID，完成时同事务保存。`POST /api/v1/commerce/sessions` 从 `require_principal` 获取主体。

- [x] **Step 1: Write failing tests.** `test_session_migration_and_ownership` 检查会话、已见表、轮次表、会话租约列、外键与唯一键，`0004→0005→0004→0005` 可逆；两个真实账号交叉访问返回 404，本人过期返回 `session_expired`，刷新 JWT 后主体不变。`test_turn_claim_persists_and_recovers` 验证同轮同参回放、异参冲突、处理中重试、租约到期恢复；不同 `turn_id` 在有效租约期间不能并行更新同一会话。旧 `lease_token` 的 `assert_current_turn` 和 `complete_turn` 失败，完成响应、购物状态与实际展示 ID 一起提交；会话 API 无自报已见入口。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_shopping_session_mysql.py tests/test_commerce_auth_api.py -q`；预期迁移或路由不存在而失败。
- [x] **Step 3: Implement.** `commerce_sessions` 默认 24 小时，增加 `active_turn_id/turn_lease_token/turn_lease_expires_at`；`commerce_turns` 保留 `processing/completed`。`claim_turn` 在短事务中锁定会话并取得单会话租约，然后在锁外执行模型/事实查询；重复同参读已完成结果，处理中返回可重试状态，租约过期用新随机令牌接管。`assert_current_turn` 由提案/预览服务在自己的事务中调用；`complete_turn` 比对令牌和会话版本，原子保存经 schema 校验的响应、状态和已见来源并清理租约。旧执行者不能提交，恢复后的原轮次可重新计算。同会话 `turn_id` 唯一，异参 409；迁移保持 InnoDB/utf8mb4/UTC `DATETIME(6)`，交易路由不接受请求体主体。
- [x] **Step 4: Run green checks.** 两个新测试文件通过且零跳过，`/health`、公开目录和旧推荐的现有 API 测试仍通过。
- [x] **Step 5: Commit.** 提交 `feat: persist owned shopping sessions`。

## Task 3：确定性选品、比较与轮次 HTTP 契约

**Files:** 新增 `commerce_orchestrator/{intent,discovery,compare,service}.py`、`tests/test_commerce_discovery.py`、`tests/test_commerce_turns_mysql.py`；修改 `api/commerce.py`、`models/commerce.py`、`api/runtime.py`。

**Interfaces:** `IntentUpdate` 定义在 `commerce_orchestrator/schemas.py`，`ShoppingIntent.apply(update: IntentUpdate) -> ShoppingIntent`；`CommerceOrchestrator.turn(principal: Principal, session_id: str, turn: TurnRequest) -> TurnResponse`；`compare(principal, session_id, product_ids: list[str]) -> CompareResponse`。`POST /sessions/{id}/turns` 总返回设计 §5.4 的统一 envelope；`TurnRequest` 可带明确的 `selected_product_id`、`selected_sku_id`，由本服务校验 SKU 是已见 Product 的实际组合、重新报价，并在响应展示后记录所选 SKU；`POST /sessions/{id}/compare` 只接收已见且同类目的 2–3 个 Product。

- [x] **Step 1: Write failing tests.** `test_turn_merges_explicit_constraints_and_replays` 检查 2000→1500 元覆盖、缺条件只问一个问题、查单/售后为 `unsupported`、同轮同参回放且不再次调用下游、同轮异参 409、并发同轮只有一个有效结果。`test_discovery_compare_are_grounded` 用两个真实类目断言短名单 ≤3、不同类目不强比、数字单位相同才比较、缺失值为“未提供”；`test_seen_ids_must_come_from_server_response` 断言只把实际展示的 Product/SKU 写入已见表，选择 SKU 时校验其属于已见商品并重新报价，错类目、超预算、未见 ID 不能进入比较或加购来源。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_commerce_discovery.py tests/test_commerce_turns_mysql.py -q`；预期新编排或端点不存在而失败。
- [x] **Step 3: Implement.** 先用可解释的确定性解析和排序交付可运行只读路径；用户明示约束才作硬条件，无法确认的文本转 `clarify`，模型推断只作软偏好。持久轮次请求哈希包含规范化消息、显式筛选、选择和确认字段；生成响应后与已见记录一起落库，客户端自报商品 ID 不会变成“已见”。本任务的 `cart_mutation`、`checkout` 返回统一 envelope 的 `unavailable`，待任务 5、6 接入后开放。
- [x] **Step 4: Run green checks.** 新测试与任务 1–2 测试全部通过；旧 `/api/v1/recommend` 与 `/graph` 形状保持原测试结果。
- [x] **Step 5: Commit.** 提交 `feat: add grounded commerce discovery`。

## Task 4：按主体唯一的持久购物车与实时读车

**Files:** 新增 `0006_cart.py`、`cart/{models,schemas,service}.py`、`tests/test_cart_mysql.py`、`tests/test_cart_api.py`；修改 `api/commerce.py`、`api/runtime.py`、`models/commerce.py`。

**Interfaces:** `CartService.get_or_create(principal: Principal) -> CartView` 与 `get_cart(principal: Principal) -> CartView`；API `GET /sessions/{id}/cart` 先调用 `get_owned`。`carts.principal_key` 唯一，`cart_lines(cart_id, sku_id)` 唯一，`quantity > 0`，车版本从 1 起；同次迁移建立 `pending_actions`，包括可空 `source_turn_id`、`confirmation_request_hash`、`UNIQUE(session_id, source_turn_id)` 和生成列 `active_session_id=CASE WHEN status='pending' THEN session_id ELSE NULL END` 的唯一索引，任务 5/6 直接使用这些约束。

- [x] **Step 1: Write failing tests.** `test_cart_migration_and_unique_owner` 检查表约束、可空 `source_turn_id` 的同会话唯一键、同一用户跨会话一辆车、并发创建只有一辆、他人会话/cart ID 404、同会话至多一个 active pending action，以及 `0005→0006→0005→0006`；刷新令牌后仍读到同一辆车，会话过期不删除车。`test_cart_read_uses_current_facts` 改价、下架、缺价、库存不足/未知后读车显示现行状态；任一行无可信价格则 `current_subtotal=null`、`checkout_eligible=false`，不能以历史快照算待付款。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_cart_mysql.py tests/test_cart_api.py -q`；预期表或服务不存在而失败。
- [x] **Step 3: Implement.** 用唯一键处理跨会话建车竞态；每行只保存展示快照与 `last_seen_*`，有效小计每次批量读 Task 1 事实重算。缺货行保留，`GET cart` 不预留或修改库存，`/turns` 的 `cart_read` 复用同一结果并保持统一 envelope；读依赖故障返回明确不可用，不把快照降级为现价。
- [x] **Step 4: Run green checks.** 新测试全通过且零跳过；现有目录/价格/库存测试不回归。
- [x] **Step 5: Commit.** 提交 `feat: persist one cart per principal`。

## Task 5：动作提案与原子确认写车

**Files:** 修改 `cart/service.py`、`catalog/service.py`、`pricing/service.py`、`inventory/service.py`、`api/commerce.py`、`models/commerce.py`；新增 `tests/test_cart_confirmation_mysql.py`、`tests/test_cart_confirmation_api.py`。

**Interfaces:** `CartService.propose_action(principal, session_id, kind, target, expected_cart_version, source_turn: TurnClaim | None = None) -> PendingActionView`；`confirm(principal, session_id, action_id, approved: bool, idempotency_key: str) -> CartConfirmationView`。`source_turn` 只由 `/turns` 的已声明轮次传入，服务调用 `ShoppingSessionService.assert_current_turn` 后以 `source_turn.turn_id` 写 `source_turn_id`；直接入口传 `None`。新增三个领域的窄接口 `CatalogService.lock_sale_facts(db: AsyncSession, sku_ids: list[str])`、`PricingService.lock_prices(db, sku_ids)`、`InventoryService.lock_availability(db, sku_ids)`；调用方传入同一事务，接口只读/锁定自己的表、不提交也不跨域写表。

- [x] **Step 1: Write failing tests.** `test_propose_requires_seen_sku_and_explicit_confirmation` 断言 `/turns` 的 `cart_mutation` 产生提案，未见 SKU、无价、停用、未知/不足库存不可产生可确认 add，未确认/取消不写车，新提案使旧 pending 失效。同一 `(session_id, source_turn_id)` 租约恢复时仅复用仍有效的原提案；原提案已失效时要求新轮次。过期/被接管的旧租约不能创建提案，异参冲突；新轮次仍能失效旧 pending。`test_confirm_rechecks_and_is_idempotent` 检查按钮 `/actions/{action_id}/confirm` 同键同参回放、同键异参 409、双击只加一次、多端版本冲突、价格/规格/可售量变化需新摘要重确认、仅库存内部版本变但数量/状态未变可继续；跨用户 action/cart 404，依赖故障零写入。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_cart_confirmation_mysql.py tests/test_cart_confirmation_api.py -q`；预期确认接口不存在或写入断言失败。
- [x] **Step 3: Implement.** 直接使用任务 4 的 `pending_actions` 约束；带 `source_turn` 的提案先锁会话并验证租约/版本，再按固定顺序锁车与旧 pending。相同来源先查询原动作并按规范化提案哈希核对，匹配且仍有效则返回原动作、不失效它；异参冲突，原动作已失效则要求新轮次重新提案。新来源才失效旧 pending 并创建提案。确认按 `cart → pending_action → Product/SKU → CNY 价格 → 按 sku_id/location_id 排序的库存余额` 加锁，计算目标最终数量，再比较提案快照和车版本，写行、车版本、动作 `confirmation_request_hash` 与 `result_json` 后一次提交。`/turns` 的改车意图调用 `propose_action`，按钮确认调用同一确定性服务；网络调用和模型调用均在事务外，数量范围 1–配置上限，remove 为独立动作。
- [x] **Step 4: Run green checks.** 两个新测试文件和任务 1–4 的测试通过；在隔离 MySQL 中并发双击、版本冲突、改价、库存变化各重复运行以确认最终数据库状态。无跳过才算通过。
- [x] **Step 5: Commit.** 提交 `feat: confirm cart actions atomically`。

## Task 6：结账预览、交接状态机与宿主网关

**Files:** 新增 `0007_checkout.py`、`checkout/{models,schemas,service,gateway}.py`、`tests/test_checkout_mysql.py`、`tests/test_checkout_api.py`；修改 `api/commerce.py`、`api/runtime.py`、`models/commerce.py`、`config/settings.py`、`.env.example`。

**Interfaces:** `CheckoutService.preview(principal, session_id, cart_version, source_turn: TurnClaim | None = None) -> CheckoutPreview`，成功时含 `action_id`、商品小计和各行报价快照；带轮次时沿用任务 5 的有效租约与 `source_turn_id` 去重规则，直接 `/checkout-preview` 传 `None`。`confirm_handoff(principal, session_id, action_id, approved, idempotency_key) -> HandoffResult`；`HostCheckoutGateway.create_handoff(snapshot: HandoffSnapshot, request_id: str) -> Destination`。网关提供 `DemoHostCheckoutGateway`（仅 `development/test/demo`）和 `UnavailableHostCheckoutGateway`（未配置真实宿主）；响应 `handoff` 只在状态持久化为 `ready` 后产生。

- [x] **Step 1: Write failing tests.** `test_preview_rejects_empty_or_unbuyable_cart` 断言 `/turns` 的 `checkout` 和 `/checkout-preview` 都生成预览，空车/不可买时返回逐行原因、商品小计而非最终应付、旧车版本冲突；同一轮恢复仅复用仍有效的原交接提案，失效后要求新轮次，旧租约不能预览并创建动作。`test_handoff_rechecks_and_recovers_timeout` 断言按钮 `/checkout-handoff` 的改价/库存变化需重确认；本地先保存一份 `preparing` 快照，宿主超时重试用相同 `handoff_id/request_id` 得同一目的地；非法 scheme/host/path 拒绝；交接失败不返回 URL。`test_no_host_is_503_in_production` 断言生产缺宿主时不消费动作且无假 URL，演示网关响应显式标 `demo`；跨用户交接记录不可读。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_checkout_mysql.py tests/test_checkout_api.py -q`；预期迁移/服务不存在而失败。
- [x] **Step 3: Implement.** `/turns` 的结账意图与 `/checkout-preview` 共用 `preview`；带 `source_turn` 时在短事务中先验证租约/版本和已有来源动作，再按任务 5 的锁序重新核价和查库存，新来源才失效旧动作并保存新提案；生产网关不可用时，在消费动作前返回 503。`checkout_handoffs.action_id` 唯一，快照为已确认商品/规格/数量/CNY 价格/车版本的不可变 JSON 与哈希；本地事务消费动作、写 `preparing`，提交后再调用宿主，随后单独事务转 `ready/failed`。重试先查本地记录并沿用同一宿主幂等请求，不重复消费动作；`Destination` 只允许服务端配置的 HTTPS host/path 或受控内部路由。真实宿主的服务鉴权、幂等回读和重新核价/库存契约未确定前不得在 production 启用网关。
- [x] **Step 4: Run green checks.** 新测试、`0006→0007→0006→0007` 迁移往返、任务 1–5 测试通过且零跳过；模拟宿主网络只用受控替身。
- [x] **Step 5: Commit.** 提交 `feat: persist and recover checkout handoffs`。

## Task 7：独立 Commerce StateGraph、受约束模型节点与聊天确认

**Files:** 新增 `commerce_orchestrator/{graph,nodes,model}.py`、`tests/test_commerce_graph.py`、`tests/test_commerce_model.py`、`tests/test_commerce_chat_confirmation.py`；修改 `commerce_orchestrator/{intent,schemas,service}.py`、`api/commerce.py`、`api/runtime.py`、`main.py`、`models/commerce.py`。

**Interfaces:** `IntentModel.parse(message, current: ShoppingIntent) -> IntentUpdate`、`CandidateReranker.rank(candidate_ids, facts, soft_preferences) -> list[str]`；两者只返回提案，输出经 Pydantic/schema 校验。`CommerceGraphState` 在 `schemas.py` 定义单轮临时字段：已验证 `Principal`、`TurnClaim`、输入消息与当前条件、意图/路由、候选 ID、可信事实、响应草稿；不含原始令牌。`CommerceGraphDependencies` 在 `graph.py` 定义，注入模型与任务 1/3/4/5/6 的领域服务；`build_commerce_graph(deps: CommerceGraphDependencies)` 返回未配置 checkpointer 的已编译异步图，禁止模块级共享用户状态。`CommerceOrchestrator.turn` 在取得租约后，只有无 `respond_to_action_id` 的普通轮次调用 `graph.ainvoke`；确认/取消先由精确短语与动作 ID 门禁判定，直接复用任务 5/6 的确定性命令。

- [x] **Step 1: Write failing tests.** `test_graph_routes_only_to_intended_branch` 用注入的服务替身验证 `shop → discover → quote_filter → rank_explain`，`compare`、`cart_read`、`cart_mutation`、`checkout` 只进入对应分支；缺信息走 `clarify`，查单/售后走 `unsupported`，报价/库存故障走 `unavailable`，所有路径经 `finalize` 返回统一 envelope。`test_graph_uses_trusted_facts_and_turn_fence` 验证节点只传经验证的候选 ID 与当前 `TurnClaim` 给提案/预览服务；旧租约失败、同轮恢复不创建第二个 action，图未配置 checkpointer/`interrupt()`。`test_model_cannot_invent_or_expand_candidate_ids` 让模型返回不存在、重复、超限 ID 或伪造价格，断言只保留 Task 1 的可信候选/事实；模型超时、异常、无 Key 时使用确定性顺序与中性说明。`test_chat_confirm_uses_same_command_as_button` 断言“确认加购”“取消”与按钮共用幂等结果且图/模型调用数为零；含“改成两件再确认”、动作 ID 不匹配、动词不匹配或多待决动作只返回澄清，绝不写车/交接。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_commerce_graph.py tests/test_commerce_model.py tests/test_commerce_chat_confirmation.py -q`；预期建图、条件边、模型适配器或聊天确认门禁不存在而失败。
- [x] **Step 3: Implement.** `graph.py` 构建 `parse_intent → validate_route`，由条件边分别进入 `discover → quote_filter → rank_explain`、`compare`、`read_cart`、`propose_cart_action`、`preview_checkout`、`clarify`、`unsupported`，可信依赖失败则转 `unavailable`，所有路径再进入 `finalize`；`nodes.py` 实现异步节点并只通过注入服务获取事实或创建有 `source_turn_id` 的待确认提案，错误映射为明确状态，不在节点中执行车确认或宿主交接。`service.py` 在鉴权、声明轮次和精确聊天确认门禁之后调用 `graph.ainvoke`，完成时校验租约/会话版本并持久化响应与已见来源。使用现有 LLM 设置和异步调用路径，不让模型看 Bearer、密码或刷新令牌；提示词与 schema 只传已验证候选事实，理由只可引用目录属性。旧推荐图保留原 API 契约，不能从其 `MOCK_PRODUCTS` 或库存过滤为空后的回填路径拿交易候选。
- [x] **Step 4: Run green checks.** 三个新文件与任务 1–6 的测试通过；无 live LLM、无凭证泄漏、模型故障不产生错误交易事实。用图替换 Task 3 的临时确定性轮次路由后，原有购物会话、确认和交接 API 测试仍通过。
- [x] **Step 5: Commit.** 提交 `feat: route commerce turns through langgraph`。

## Task 8：页面事件、内部演示页与端到端验收

**Files:** 新增 `0008_commerce_events.py`、`commerce_events/{models,service}.py`、`api/commerce_demo.py`、`demo/commerce.html`、`demo/commerce.js`、`tests/test_commerce_events_mysql.py`、`tests/test_commerce_e2e.py`；修改 `main.py`、`api/commerce.py`、`api/runtime.py`、`.env.example`。

**Interfaces:** `CommerceEventService.record(principal, session_id, event_id, request_id, kind, payload) -> EventReceipt`；`POST /api/v1/commerce/events` 去重返回 `accepted/duplicate`。同源演示页只在显式开发/演示环境挂载，使用登录接口取得内存中的 access token，刷新只走 `__Host-` Cookie + CSRF；页面不保存令牌到 localStorage/sessionStorage。

- [x] **Step 1: Write failing tests.** `test_event_dedupe_and_failure_is_non_blocking` 断言相同 `event_id` 只记一次、跨用户事件拒绝、事件库故障不回滚车/交接。`test_two_category_purchase_preparation` 使用真实 MySQL 中两个类目、两名真实演示账号、模型与宿主替身，走注册/登录、**经独立 commerce 图**发现、比较、选 SKU、提案、确认加购、改价触发重确认、按新价确认、交接恢复；断言图分支可观察、聊天确认不调用图、旧租约恢复不重复提案、过程中无订单或付款成功状态，恶意主体 404。检查演示页仅在非生产开放，七种页面状态和可见曝光上报可人工验证。
- [x] **Step 2: Run red checks.** `.venv/bin/python -m pytest tests/test_commerce_events_mysql.py tests/test_commerce_e2e.py -q`；预期事件/演示页面或闭环缺失而失败。
- [x] **Step 3: Implement.** 事件输入只允许枚举 kind 与有限结构化属性，不含令牌和自由复制的完整聊天内容；数据库写入失败记审计并返回可观察失败，但业务事务已经提交、不回滚。演示页显示 `demo` 交接、商品小计与费用边界，覆盖空结果、缺货、价格变化、模型降级、写入失败和重试；生产部署由宿主前端按同一 API 契约实现，真实宿主未接通仍返回 503。
- [ ] **Step 4: Run final gates.** `.venv/bin/python -m pytest tests/ -q -rs` 全通过、MySQL/Redis 用例零跳过；独立测试库验证 `0004→0005→0006→0007→0008→0004→head`；`git diff --check` 通过。以同站点 HTTPS 手动核对演示页、Cookie/CSRF/CORS、跨用户 404、页面七种状态；检查新 commerce 图的路径与旧推荐/公开报价均按原契约运行，确认库存无效时新流程不会回填旧图商品。记录真实宿主、正式账号归属和正式限购值三个生产上线前提，不能把内部演示验收写成生产交易验收。
- [x] **Step 5: Commit.** 提交 `feat: complete commerce mvp demo flow`，并按 `superpowers:requesting-code-review` 对完整实现做独立复核。

## 交付边界

任务 1–8 完成后可验收**内部演示版购物准备闭环**。面向真实用户发布仍需正式账号归属验证与恢复、可信宿主服务身份及结账契约、宿主对不可变快照的核价/库存/过期/幂等规则、正式限购政策和前端宿主责任确认；这些外部条件未落实时，生产 `checkout-handoff` 保持不可用。

## 2026-09-30 实施状态

任务1–8的代码与自动化验证已实施，独立审查问题已修复。Task8的人工页面门禁尚未执行：Computer Use 无可用浏览器，用户明确要求启动服务并自行操作，故保留该项待用户验收。实际证据、服务入口和生产前提见 [验收记录](../verification/2026-09-30-commerce-agent-mvp.md)。
