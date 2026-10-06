# 分层重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已修订的分层设计消除反向依赖、跨模块 ORM 泄漏与构造后回填，并在合同不变的前提下完成可验证的命名空间迁移。

**Architecture:** 保留模块化单体和八个既有业务模块的内聚形态。kernel 持有共享 DTO/纯规则，sale_facts 提供事实读取能力，application 动作用例拥有跨模块事务；infra 仅由组合根装配。职责/契约调整已完成，D5已批准目录与包根搬迁；按D1/D2保留旧推荐栈为legacy及实验/指标，规则归一独立审批，本轮不退役旧栈。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy async + asyncmy、MySQL、Redis、LangGraph、Alembic、pytest、ruff、import-linter 2.15；现有 Node demo 合同测试。

**Spec:** [分层设计方案](../../layering-design.md)。执行者必须同时阅读设计与本计划；其他购物 Agent/检索功能计划不属于本次任务。

状态：Task1–13已逐项实现、独立复核及整体阶段审查，714测试通过，三门禁534/151/29零跳过；已同步主目录并提交为8779cf5。D5已确认，Task14–16的目录、包根及测试/工具/CI收尾已批准、尚未实施；D1已确认保留旧推荐栈为legacy，D2已确认保留实验与指标，Task19本轮不执行。D3/D4沿用设计既定选择；只有Task17–18的R仍需独立确认，不推送远程，不纳入原有AGENTS修改。

## Global Constraints

- **结构迁移不改业务行为**。保留现有路由前缀、请求/响应、HTTP 状态码、错误体、幂等与锁定语义。
- **旧栈保留已确认**。按D1/D2保留旧推荐调用链为legacy，以及推荐/实验/指标接口、注册、消费者和测试；本轮不执行Task19，不将保留确认解释为退役批准。
- **测试必须有真实执行证据**。记录 node ID、执行结果、跳过原因与路径映射；关键 MySQL/Redis 集成任务要求零跳过，不能只比较 skip 总数。
- **保持模块内聚**。不引入纯净 domain/repository 层、微服务、通用 Unit of Work、新业务表或额外 Agent 功能。
- kernel 无 I/O；访问 modules 的直接白名单仅为 catalog/pricing/inventory 的 schemas。基础 schemas 不反向依赖 kernel。
- modules/application 不依赖 api/infra；应用包内仅组合根可以直接引用并构造 infra。跨模块使用公共 service/schemas/ports/types，禁止直接模型访问与 ORM 返回值穿透。
- 金额保留整数分/CNY；ID 编码、UTC 类型、JSON 落库形状、幂等请求哈希、metadata 表/FK/索引不变。
- 保持顺序事实读取与既有加锁/排序/重放顺序；模型/宿主网络调用不持锁。结账 preparing 与动作消费同事务，网络之后再用第二段事务收尾。
- `demo/`、`frontend/`、`migrations/` 保持顶层；源码 + requirements 部署，不顺便改成 wheel 打包。开发/CI 使用 Python 3.12，Node 采用现有 frontend 声明的 `>=22.12`。
- pytest 沿用 `asyncio.run()`，不引入 pytest-asyncio。ruff 先只启用 E/F/I，历史告警单列，不清理无关代码。
- 真实数据库测试只使用显式的独立 `ECOM_TEST_DATABASE_URL`；Redis 使用隔离的 `ECOM_TEST_REDIS_URL`，保留按 `identity:rl:*` 清理键的既有行为，不执行 FLUSHDB。
- MySQL 集成测试串行执行；不让多个任务/测试进程共享会被 downgrade 的测试库。缺服务时停止相关验收，不以 stub 替代事务证据。
- 保留原有未提交修改、客户数据、下载数据和参考仓库；不暂存 `.env`、数据库/Redis URL、密钥、嵌套 Git 仓库。
- 目录阶段仍在隔离worktree逐项实施，复用获准的现有环境与独立测试服务。第一阶段已提交，Task14–16的D5已确认；Task17–18的R仍需独立确认，Task19因D1/D2选择保留而排除本轮执行。每项提供审核检查点，不将D5视为后续规则/退役或远程推送授权。

## Review Focus

1. 中文/Unicode 请求、naive/aware UTC、零金额、哈希字段顺序：迁移不能改变请求哈希、主体键、响应时间或金额序列化（Task 2、3）。
2. 模块导入顺序及 LangGraph `get_type_hints`：移 DTO 后必须能解析并运行图，不能用 Any 或 TYPE_CHECKING 绕过循环（Task 3、12、15）。
3. 已保存的轮次响应、归属字段与不透明 JSON 不一致、无 source_turn 的直接 API：既有回放与安全门禁仍有效（Task 4、9、10）。
4. 同主体多会话共车、租约被接管、确认提交后断线、宿主超时后并发重试：不部分提交、不重复加购/交接，不扩大事务（Task 7–10）。
5. 缺数据库/Redis、不同工作目录、静态资源路径和 JUnit 映射丢失：保留健康检查/错误合同，测试或入口缺失必须明确失败（Task 1、11、15、16）。

---

## 范围、执行顺序与审批门槛

当前源码基线提交为 `9db6b97`，Alembic 头为 `0008_commerce_events`；执行时重新核查，若源码/迁移头已变，先更新受影响任务，不创建新迁移分支。本计划不新增 revision。

| 设计步骤 | 计划任务 | 依赖与停止条件 |
| --- | --- | --- |
| 0：基线/保障 | Task 1 | 关键集成基线真实执行且零跳过，才允许开始事务重构 |
| 1：shared/kernel | Task 2–3 | 只抽取/迁契约；结构阶段保留旧路径规则差异 |
| 2：DTO/事实解耦 | Task 4–5 | 依赖 Task 2–3；业务模块不再依赖 commerce_orchestrator |
| 3：公共操作/用例/错误 | Task 6–11 | 先新增无提交公共操作，再切调用方；每个模块边界各自复核 |
| 4：装配完整化 | Task 12–13 | 先取得事务/API/装配证据，再讨论 D5 |
| 5：顶层分组 | Task 14 | **D5 明确批准后**才执行；未批准时停在 Task 13 |
| 6：ecom 包根 | Task 15 | 依赖 Task 14；同一检查点更新入口/Alembic/契约 |
| 7：tests/tools | Task 16 | 依赖 Task 15；按真实依赖分类，保留根 conftest |
| R：规则行为变更 | Task 17–18 | **R 的差异清单单独批准后**；Task 5/9 的兼容基线就绪即可安排，不必等待 Task 16 |
| L：本轮不执行 | Task 19 | **D1保留为legacy、D2保留实验与指标已确认**；不删除旧代码、路由或测试，未来退役须重新批准具体清单 |

D3 按设计保持 demo 顶层，D4 按禁止 application → infra 执行；若用户改动这两个决定，先更新设计及对应任务。任务有共享文件/接口依赖，默认逐项串行，不因为使用子代理而并行改同一文件。

## 文件边界与阶段名称

| 创建/调整的文件组 | 职责 | 最终位置 |
| --- | --- | --- |
| `shared/{errors,ids,time,principal,hashing,db}.py` | 无业务码异常基类、ID/时间/主体/哈希、唯一 Base | `ecom/shared/` |
| `kernel/{intent,quotes,constraints,errors}.py` | 购物约束/报价 DTO、纯规则、流程错误 | `ecom/kernel/` |
| `commerce_orchestrator/{contracts,state,actions,ports,guards}.py` | 用例 DTO、图 state、事务协调、模型端口/安全包装 | `ecom/application/shopping/` |
| `sale_facts/service.py` | 非锁定/同事务锁定报价，无表 | `ecom/modules/sale_facts/service.py` |
| `catalog/ports.py`、`catalog/service.py` | PriceReader、SKUExistenceReader、CatalogSKUReader | `ecom/modules/catalog/` |
| cart/checkout/sessions 的 schemas/service | 无 ORM 的公共 DTO、无提交操作、持久化原子能力 | `ecom/modules/{cart,checkout,sessions}/` |
| `checkout/ports.py`、`checkout/gateway.py` | 消费者端口与暂存的网关实现 | 端口 → modules/checkout；实现 → infra |
| `api/{errors,http_config,runtime}.py` | 兼容错误映射、HTTP 配置、组合根/提供器 | `ecom/api/`；runtime → deps |
| `db/session.py`、`commerce_orchestrator/llm_adapter.py` | 迁移前的具体 DB/LLM 适配器 | `ecom/infra/{db,llm}.py` |
| `tests/layering_*`、`tests/test_layering*.py` | 基线/映射/边界工具及其正负例 | Task 16 再分类到 unit/integration/support |
| `agents/orchestrator/models.schemas/services/infrastructure` | 保留旧推荐链，绝不反向污染新业务层 | `ecom/legacy/`（精确映射见 Task 14） |

Task 1–13 的代码使用现有顶层包名；Task 14 才分 groups，Task 15 才统一 `ecom.`。不要提前写入导入不到的 `ecom.*`。此表中的新包同时创建空 `__init__.py`，不通过包级重导出隐藏依赖。

下列 Files 字段按该任务执行时的路径列举；搬迁后的任务采用新路径。源码中要修改的同类调用方用 `rg` 逐项形成清单；每项检查点保存该清单，而非未经核查替换整个仓库。

## 共享接口台账

这里固定跨任务的名字和形状，不提供实现体。除标明的纯函数/构造器外，服务 I/O 方法均为 async；`Factory = async_sessionmaker[AsyncSession]`、`JsonObject = dict[str, Any]`。下文简写的 `db` 参数统一为 `AsyncSession`，`principal` 统一为 `Principal`，实现时必须写出类型注解。公共模块通过字面量 `__all__` 声明入口；ORM 类只在自己的 models 和内部服务之间可见。

### C1：基础与纯规则（Task 2–3）

- `shared.errors.DomainError(Exception)`；`new_id() -> str`、`utcnow() -> datetime`、`mark_utc(value: datetime) -> datetime`，保留原 `UtcDateTime` 行为。`shared.db.Base` 与 `TimestampMixin` 不复制定义。
- `Principal` / `make_principal(issuer: str, user_id: str) -> Principal` / `principal_key_for(issuer: str, subject: str) -> str` 原样迁移；`request_hash(data: JsonObject) -> str` 原样迁移。
- `ShoppingIntent` / `IntentUpdate` / `HardCondition` / `SKUQuote` / `CandidatePage` 字段取当前 schema 原定义；不能加入本计划之外的新字段。
- `evaluate_purchasability(sku: SKUDetailView | None, price: PriceView | None, availability: AvailabilityView | None) -> str` 按设计 §4.2；`matches_product_constraints(intent: ShoppingIntent, product: ProductView, category_attributes: Sequence[AttributeView]) -> bool`、`matches_budget(intent: ShoppingIntent, price: PriceView | None) -> bool` 按 §4.3。
- 结构阶段 `kernel/quotes.py` 保留 `evaluate_legacy_api_purchasability(...) -> str`、`evaluate_legacy_cart_purchasability(...) -> str` 两个临时包装，准确复制当前两个较弱实现；主函数用于当前 facts 路径。R 批准前不让 API/cart 偷偷改用新口径，Task 17 统一后删除包装。
- `kernel.errors.CommerceDependencyError(DomainError)`；`CommerceError(code: str, details: JsonObject | None = None)` 在 Task 11 迁入，移除内层 status_code。

### C2：会话/轮次（Task 4、7）

- `TurnRequest` / `TurnResponse` 暂归 `commerce_orchestrator/contracts.py`，HTTP API 复用；字段/限制/序列化与 `models/commerce.py` 原定义相同。
- `TurnCompletion(session_id: str, turn_id: str, response: JsonObject, state: JsonObject = {}, product_ids: list[str] = [], sku_ids: list[str] = [])`；空集合使用 `Field(default_factory=...)`，不是共享可变默认值。
- `TurnReplay(session_id: str, turn_id: str, response: JsonObject)`；`claim_turn(...) -> TurnClaim | TurnReplay`、`complete_turn(claim: TurnClaim, completion: TurnCompletion) -> None` 参数名称和现有租约语义不变。
- `ShoppingSessionService.lock_owned_session(db: AsyncSession, session_id: str, principal_key: str) -> ShoppingSessionView`；`has_seen_sku(db: AsyncSession, session_id: str, sku_id: str) -> bool`；`assert_current_turn(db: AsyncSession, claim: TurnClaim) -> None`。这三个方法不另开事务、不返回 ORM。

### C3：catalog/事实能力（Task 5–6）

- `SKUExistenceReader(Protocol).require_sku_exists(db: AsyncSession, sku_id: str) -> None`；缺失抛 `CatalogNotFoundError`。`CatalogSKUReader(factory: Factory)` 实现端口且不持 pricing。
- `PricingService(factory: Factory, sku_reader: SKUExistenceReader)`、`InventoryService(factory: Factory, sku_reader: SKUExistenceReader)`；所有构造点一起调整，不能用可选默认值偷偷构造 catalog。
- `SaleFactsReader(catalog: CatalogService, pricing: PricingService, inventory: InventoryService)`：`quote_many(sku_ids: list[str]) -> dict[str, SKUQuote]`；`lock_quotes(db: AsyncSession, sku_ids: list[str]) -> tuple[dict[str, SKUQuote], dict[str, SaleFactView]]`。
- `CommerceFactsReader(catalog: CatalogService, facts: SaleFactsReader)`：保留 `scan(intent: ShoppingIntent, max_scanned: int, page_size: int = 20) -> CandidatePage`，`quote_many` 委托 facts。
- 锁定路径在 R 前采用 C1 的 legacy cart 规则；不锁路径用原 facts 规则。`get_skus → get_prices → get_availability` 与 `lock_sale_facts → lock_prices → lock_availability` 顺序不变。

### C4：cart 公共事务操作（Task 7）

新 DTO 只供内部公开服务使用，不改变 HTTP 视图：

- `CartLineSnapshot(line_id: str, sku_id: str, quantity: int, product_name_snapshot: str, sku_options_snapshot: list[JsonObject], image_url_snapshot: str | None, last_seen_unit_amount_minor: int | None, last_seen_price_version: int | None)`：对应现有 CartLine 列，不带 ORM。
- `CartSnapshot(cart_id: str, principal_key: str, version: int, lines: list[CartLineSnapshot])`。
- `PendingActionRecord`：`action_id/session_id/principal_key/cart_id/kind/proposal_hash/status: str`，`source_turn_id/confirmation_key/confirmation_request_hash: str | None`，`expected_cart_version: int`，`payload/summary: JsonObject`，`expires_at/created_at: UtcDateTime`，`result: JsonObject | None`。明确映射 id → action_id、payload_json → payload、quote_snapshot_json → summary、result_json → result；checkout 比较/展开 summary，不再访问原 ORM 属性名。computed active_session_id 仅在模块内部用于唯一约束。
- `ActionProposalCommand(kind: Literal['add','set_quantity','remove'], target: JsonObject, expected_cart_version: int, source_turn_id: str | None = None)`；不携带 TurnClaim。
- `CartService(factory: Factory, facts: SaleFactsReader, max_quantity: int = 10, action_ttl_seconds: int = 600)`，原 get_cart/get_or_create 公开入口及其按需创建空车行为保留。
- `lock_cart_snapshot(db, principal: Principal) -> CartSnapshot`；`lock_pending_action(db, principal, session_id: str, action_id: str) -> PendingActionRecord`；`find_action_owner(db, principal, action_id: str) -> str` 返回 session_id，未知/非本人仍 not_found。
- `propose_action_in_transaction(db, principal, session_id: str, command: ActionProposalCommand, intent: ShoppingIntent) -> PendingActionView`；`confirm_in_transaction(db, principal, session_id: str, action_id: str, approved: bool, idempotency_key: str) -> CartConfirmationView`。
- `find_source_action_in_transaction(db, principal, session_id: str, source_turn_id: str | None, digest: str) -> PendingActionRecord | None`。
- `create_pending_action_in_transaction(db, principal, session_id: str, cart: CartSnapshot, kind: str, target: JsonObject, snapshot: JsonObject, digest: str, source_turn_id: str | None = None) -> PendingActionRecord`。这两条返回 record 保留 checkout 所需 created_at/summary；cart 自己把 record 映射成既有 PendingActionView，不向 HTTP 暴露新增内部字段。
- `confirmation_replay_in_transaction(db, principal, action: PendingActionRecord, approved: bool, key: str) -> JsonObject | None`；`resolve_action_in_transaction(db, principal, session_id: str, action_id: str, status: str, approved: bool, key: str, result: JsonObject) -> None`；`invalidate_action_in_transaction(db, principal, session_id: str, action_id: str) -> None`。
- `get_action(principal, session_id: str, action_id: str) -> PendingActionView`、`is_only_pending(principal, session_id: str, action_id: str) -> bool` 不再返回 ORM；会话归属由 application 在外层检查。
- 所有上述 db 参数均为同一个 AsyncSession；事务方法不调用 factory/commit/rollback。同步纯方法 `quote_snapshot(quote: SKUQuote, sale: SaleFactView | None) -> JsonObject`、`assert_buyable(quote: SKUQuote, quantity: int) -> None` 保留为 cart 公共入口供 checkout 使用；内部读取 ORM 后先转换 DTO，不在 checkout 再复制规则。

### C5：checkout 与 application 动作用例（Task 8–10）

- `HostCheckoutGateway` 暂归 `checkout/ports.py`，`available: bool`、`create_handoff(snapshot: HandoffSnapshot, request_id: str) -> Destination` 原定义不变。
- `HandoffAttempt(snapshot: HandoffSnapshot, replayed: bool)`；handoff ID 直接取 snapshot.handoff_id，不再生成第二个 ID。
- `CheckoutService(factory: Factory, cart: CartService, gateway: HostCheckoutGateway, allowed_hosts: tuple[str, ...] = (), allowed_path: str = '/checkout/', timeout_seconds: float = 5.0)`。
- `preview_in_transaction(db, principal, session_id: str, cart_version: int, source_turn_id: str | None = None) -> CheckoutPreview`。
- `prepare_handoff_in_transaction(db, principal, session_id: str, action_id: str, approved: bool, idempotency_key: str) -> HandoffAttempt | HandoffResult`：可直接返回拒绝/回放/重确认结果，否则保存 preparing + 消费动作并返回 attempt。
- `request_destination(attempt: HandoffAttempt) -> Destination | None` 异步调用注入网关并做现有白名单校验；`finalize_handoff_in_transaction(db, principal, attempt: HandoffAttempt, destination: Destination | None) -> HandoffResult`。
- `get_owned_in_transaction(db, principal, session_id: str, handoff_id: str) -> HandoffResult`，通过 cart.find_action_owner 核对关系，不能导入 cart.models。
- `CartActionService(factory: Factory, sessions: ShoppingSessionService, cart: CartService, checkout: CheckoutService | None)` 提供：
  - `propose_action(principal, session_id: str, kind: str, target: JsonObject, expected_cart_version: int, source_turn: TurnClaim | None = None) -> PendingActionView`。
  - `confirm(principal, session_id: str, action_id: str, approved: bool, idempotency_key: str, source_turn: TurnClaim | None = None) -> CartConfirmationView`。
  - `preview_checkout(principal, session_id: str, cart_version: int, source_turn: TurnClaim | None = None) -> CheckoutPreview`。
  - `confirm_handoff(principal, session_id: str, action_id: str, approved: bool, idempotency_key: str, source_turn: TurnClaim | None = None) -> HandoffResult`。
  - `get_handoff(principal, session_id: str, handoff_id: str) -> HandoffResult`、`get_action(principal, session_id: str, action_id: str) -> PendingActionView`、`is_only_pending(principal, session_id: str, action_id: str) -> bool`。
- CartActionService 每个写操作先在调用方事务中校验会话/主体，再按原路径核验 source_turn；购物车 add 的 seen 检查与动作创建使用同一 session。无 source_turn 也不能省略会话归属检查。

### C6：图、组合根与架构工具（Task 1、12–13）

- `CommerceGraph(Protocol).ainvoke(state: CommerceGraphState) -> CommerceGraphState`；图是其 LangGraph 实现，orchestrator 不依赖具体 infra。
- `CommerceGraphDependencies` 保留 discovery/facts/comparison/catalog/sessions/cart/intent_model/reranker，新增 actions，删除 checkout；模型包装由组合根显式传入。
- `CommerceOrchestrator(sessions: ShoppingSessionService, graph: CommerceGraph, actions: CartActionService | None, comparison: CompareService)`，保留 turn/compare 的现有公开入口，不再 configure_graph/构造后赋值。
- `DatabaseResources(engine: AsyncEngine, session_factory: Factory)`；`db.session.create_database(database_url: str) -> DatabaseResources`，旧 create_session_factory 委托它，供尚未迁移的工具使用。
- `async create_runtime(settings: Settings | None = None, *, session_factory: Factory | None = None, redis_client: Any = None, gateway: HostCheckoutGateway | None = None, model: IntentModel | None = None) -> ApiRuntime`；注入模型必须同时实现 CandidateReranker。默认保留当前 get_settings 行为。调用点必须 await，主应用在 lifespan 中构造，不在 import 时运行事件循环。
- Runtime 记录 owned_engine/owned_redis，`aclose() -> None` 只关闭自己创建的资源且可重复调用；注入的外部资源由测试/调用方关闭。
- create_runtime 内用 AsyncExitStack 注册每个已创建资源的关闭操作；构造失败先异步清理，成功时把清理所有权转给完整 Runtime。已知配置缺失返回带既有错误原因的降级 Runtime，非预期构造异常清理后重新抛出，不以空组件吞掉异常。
- 装配接口取舍：保持同步入口可以少改调用点，但资源失败清理必须散到调用方，容易漏掉返回前已创建的 DB/Redis；选异步入口统一清理/转移所有权，代价是所有构造桩与 lifespan 要一起改为 await。这只是内部装配协议变化，不改变 HTTP 或数据库合同。
- `tests/layering_reports.py`：`compare_results(baseline_dir: Path, current_dir: Path, path_map: dict[str, str], allowed_changes: dict[str, str]) -> list[str]`；`assert_zero_skips(report_path: Path) -> None`。新增且通过的回归用例、原失败/跳过转为通过属于非退化，允许；新增跳过/失败/错误、原通过退化、未映射删除及未解释跳过原因变化必须拒绝。批准的行为/退出变更值必须包含 R/L 审批编号与原因，结构阶段传空字典。
- 同一报告模块提供 `new_lint_violations(baseline: list[JsonObject], current: list[JsonObject], path_map: dict[str, str]) -> list[JsonObject]`；按映射后的路径、规则码、源文本与出现次数比较，不因搬迁行号变化误报，也不让新增同类违规被旧记录抵消。CLI `python -m tests.layering_reports lint --baseline PATH --current PATH [--path-map PATH]` 返回 0 表示无新增，1 表示存在新增，其他状态为工具错误。
- `tests/layering_rules.py`：`check_import_boundaries(root: Path, layout: str) -> list[str]`，layout 为 `original|grouped|packaged`；`generate_import_contracts(root: Path, layout: str) -> str` 生成完整 TOML，扫描到的模块清单不能漏项。

## 通用任务审核与提交策略

每项必须经历“写新回归 → 确认预期失败 → 最小实现 → 定向测试 → 合同/相关集成复核 → 审核检查点”。红灯必须来自该任务未实现的行为，不接受缺依赖/服务、错误工作目录或旧故障充当红灯。

每个检查点运行 `git diff --check`，核对 `git status --short` 和任务文件清单；保存验证命令、测试结果及路径映射。只在获授权后对清单内文件单独 `git add` / `git commit`，不执行 `git add .`。任务给出的提交标题是建议，不是当前提交授权。

每项执行前保存该任务文件的局部补丁/基线；验收失败时只恢复该项自有改动及其调用方联动，重跑上一个检查点，不回退用户原有修改。结构阶段不操作业务数据、无数据库回迁；目录/包根的入口、静态路径、架构配置与模型注册必须整体回滚。实际回滚操作仍须按环境权限确认，不能以 `git reset --hard` 清空共享工作区。

### Task 1：建立可比较的合同基线和三类 CI 门禁

**Files:** Create `requirements-dev.txt`、`pyproject.toml`、`tests/layering_reports.py`、`tests/test_layering_reports.py`、`tests/test_layering_baseline.py`、`tests/run_demo_contracts.cjs`、`.github/workflows/layering.yml`、`tests/fixtures/layering/{routes,openapi,metadata,errors,lint-baseline}.json`；Modify `.gitignore`、`tests/conftest.py`。

**Interfaces:** 产出 C6 的报告比较接口；新增 pytest 选项 `--layering-artifacts=PATH`、`--require-integration-services`，markers 为 mysql/redis；artifact 目录只存测试/路由/metadata 合同，不存数据或配置 URL。

- [x] **Step 1：写报告与分类的失败测试。** `test_equal_skip_count_does_not_hide_missing_test` 使用两份各一个 skipped 的报告，但当前少一个原 node ID，必须报告遗漏；`test_zero_skip_gate_rejects_skip`、`test_path_mapping_preserves_parametrized_node_ids` 检查门禁。`tmp_path` 是现有 pytest 夹具。

```python
def test_zero_skip_gate_rejects_skip(tmp_path):
    report = tmp_path / 'results.xml'
    report.write_text('<testsuite><testcase name="required"><skipped/></testcase></testsuite>')
    with pytest.raises(AssertionError, match='skipped'):
        assert_zero_skips(report)
```

- [x] **Step 2：验证红灯。** Run `python -m pytest tests/test_layering_reports.py -q`；Expected：仅因比较/零跳过接口未实现失败，不能因 pytest 缺失继续。如果缺依赖，先获准在 Python 3.12 开发环境安装现有 requirements 与开发工具，再重跑。
- [x] **Step 3：实现基线设施。** import-linter 固定 `2.15`；pytest/ruff 在 Python 3.12 解析并验证后把实际版本写成 `==`，不猜版本。pyproject 首先只配置 pytest/ruff E/F/I，不写 ecom 契约。收集时按 fixture 闭包给真实 MySQL/Redis 用例加 marker，并将 node_id 作为 JUnit user property；纯桩限流测试不标 redis。报告比较检查 node ID、结果和 skip 原因，拒绝未解释的删除/新增 skip。`--require-integration-services` 要求所选集成类别的显式环境变量，不能读开发 `.env.local` 偷渡验收。
- [x] **Step 4：加入合同基线测试和 JS runner。** 在业务修改前从旧源码采集并冻结 routes/openapi/metadata/errors fixture；`test_routes_and_openapi_match_baseline`、`test_metadata_signature_matches_baseline`、`test_missing_configuration_responses_match_baseline` 覆盖现有 main 的路由/OpenAPI、注册的八模块 metadata、无数据库健康检查/503。除时间/request_id 等既有随机值外不归一字段。runner 用 Node vm 提供现有脚本所需 load/print/quit，从仓库根解析 demo 路径，不修改前端代码。
- [x] **Step 5：建立并验证基线。** Run `python -m pytest tests/ --collect-only -q --layering-artifacts=.artifacts/layering/baseline`；Run `python -m pytest tests/ -q --junitxml=.artifacts/layering/baseline/results.xml --layering-artifacts=.artifacts/layering/baseline`；Run `node tests/run_demo_contracts.cjs`。Expected：完整清单、合同和结果已保存，JS 输出 `10 desktop chat contracts passed`。关键 MySQL/Redis 基线必须在独立测试服务上实际执行且零跳过，否则不进入 Task 7–10。
- [x] **Step 6：接入三类 CI 并审核检查点。** unit：`-m 'not mysql and not redis'`；mysql：`-m 'mysql and not redis'`；redis：`-m redis`，后者提供 MySQL+Redis；每个专用集成任务使用 require flag 并调用 assert_zero_skips。生产 URL/客户数据不得进入 CI，Redis 不公开端口，不使用共享实例。忽略 `/.artifacts/layering/`，提交小型、去敏的路由/metadata 基线 fixture，不提交全量运行产物。建议标题 `test: establish layering migration contracts`。
- [x] **Step 7：固定 lint 基线并接入新增违规门禁。** 用 `ruff check . --output-format json` 采集旧源码 E/F/I 告警，退出 1 只表示已有告警，其他非零状态必须失败；去掉绝对路径/机器信息后冻结 lint-baseline.json。新增 `test_added_lint_occurrence_is_not_hidden` 与路径映射测试。CI 采集 current.json 后运行上述 lint CLI 比较，Expected：旧告警不阻断、新增告警阻断、搬迁不改变出现次数；不使用全局 ignore 或自动 fix。

完成证据：本 worktree 的 `.superpowers/sdd/2026-10-06-layering-implementation/task-1-report.md` 与 `task-1-review.md`；226 passed/零跳过、三门禁与聚合通过、JS10、新增lint0；独立 reviewer 双判定通过，云端 Actions 未执行。

### Task 2：抽取 shared 原语并保持唯一 Base

**Files:** Create `shared/__init__.py`、`shared/{errors,ids,time,principal,hashing,db}.py`、`tests/test_shared_primitives.py`、`.importlinter.stage.toml`、`tests/test_layering.py`；Modify `db/base.py`、`identity/types.py`、`commerce_orchestrator/utils.py`、所有原 Base/Principal/hash 的业务调用方、`.github/workflows/layering.yml`。

**Interfaces:** 消费当前基础定义；产出 C1 原语。旧入口短期只重导出相同对象，不能构造第二份类/metadata。

- [x] **Step 1：写身份/UTC/hash/metadata 的失败测试。** `test_base_alias_has_single_metadata`、`test_principal_key_is_unchanged`、`test_unicode_hash_and_order_are_unchanged`、`test_utc_round_trip_is_unchanged`。冻结当前代表性输入的输出值作为 fixture，并比较所有现有 models 的 metadata 身份。

```python
def test_base_alias_has_single_metadata():
    from db.base import Base as old_base
    from shared.db import Base as shared_base
    assert old_base is shared_base
    assert old_base.metadata is shared_base.metadata
```

- [x] **Step 2：Run `python -m pytest tests/test_shared_primitives.py -q`，Expected：新 shared 导入尚不存在导致预期红灯。**
- [x] **Step 3：逐个原语搬实现。** 按 C1 创建文件；`mark_utc` 只改函数归属/公开名字，保留 `_mark_utc` 的兼容别名；legacy 的 naive 时间/浮点价格不顺便修正。先更新 model 的 Base 导入，再更新 DTO 时间、Principal 与哈希引用。
- [x] **Step 4：Run `python -m pytest tests/test_shared_primitives.py tests/test_identity_core.py tests/test_commerce_facts.py tests/test_layering_baseline.py -q`，Expected：通过且 metadata/HTTP 合同不变。** MySQL 的 catalog/pricing/inventory/identity 相关现有测试也需通过专用门禁。
- [x] **Step 5：创建并启用阶段配置/CLI wrapper，审核检查点。** 枚举实际存在的所有顶层应用包，先启用 shared 不得依赖 config/业务包的 forbidden；wrapper 调用真实 `lint-imports --config .importlinter.stage.toml --no-cache`，断言 returncode==0，缺工具/配置直接失败。Task 3/4/5/6/9/10/11/12 每修复一条边界即增量加入，Task 13 才补全角色 AST；不提前启用仍未修复的全模块边界。记录旧入口消费者与明确残留违规，更新消费者后才能移除别名。建议标题 `refactor: extract shared primitives without metadata changes`。

完成证据：shared 同对象别名/唯一 metadata 及真实阶段 CLI 已建立；243 passed、零跳过，合同不变、新 lint0。独立 reviewer：Spec compliant / Approved（无发现）。

### Task 3：购物共享 DTO、纯规则与可解析的图 state

**Files:** Create `kernel/__init__.py`、`kernel/{intent,quotes,constraints}.py`、`commerce_orchestrator/contracts.py`、`commerce_orchestrator/state.py`、`tests/test_kernel_contracts.py`；Modify `commerce_orchestrator/{schemas,graph,nodes,service,intent,model,facts,discovery,compare}.py`、`cart/schemas.py`、`models/commerce.py`、`api/commerce.py`。

**Interfaces:** 消费 C1；产出 C1 购物 DTO/规则与 C2 Turn DTO。TurnRequest/Response 的旧入口仅同对象重导出；其余 HTTP 输入仍在 models/commerce，Task 14 再搬。

- [x] **Step 1：写结构和纯规则失败测试。** `test_turn_contract_is_same_object_and_same_json`、`test_graph_hints_resolve_real_contracts`、`test_reason_priority_includes_negative_stock`、`test_legacy_quote_paths_keep_existing_differences`、`test_amount_zero_and_utc_json_match_baseline`。

```python
def test_graph_hints_resolve_real_contracts():
    hints = get_type_hints(CommerceGraphState)
    assert hints['turn'] is TurnRequest
    assert hints['response'] is TurnResponse
    assert hints['current'] is ShoppingIntent
```

- [x] **Step 2：Run `python -m pytest tests/test_kernel_contracts.py -q`，Expected：新归属或真实图类型尚未存在。**
- [x] **Step 3：迁类型并抽函数。** 按 C1/C2 原样迁字段，商品条件/预算输入分别定义，不复制三个基础模块 schema。旧 facts 路径使用最完整纯规则，API/cart 保留两个临时包装。state 直接导入新 contracts 与 TurnClaim，移除 get_type_hints 的 Any 兜底；基础 schemas 不 import kernel。
- [x] **Step 4：Run `python -m pytest tests/test_kernel_contracts.py tests/test_commerce_graph.py tests/test_commerce_discovery.py tests/test_commerce_model.py tests/test_commerce_read_api.py tests/test_cart_api.py -q`，Expected：图构建及实际 ainvoke 通过，既有路由输出不变。** `bool/int` 条件差异、预算等号、缺价格/未知库存/非 CNY 和多失败优先级均参数化，旧包装差异单独断言。
- [x] **Step 5：审核类型导入顺序与规则审批记录。** 强制以不同顺序导入 contracts/state/cart.schema/catalog.schema；记录 R 之前临时包装不可删除。建议标题 `refactor: assign shopping contracts and pure rules`。

完成证据：首次292 passed/零跳过，fix1公开legacy函数归kernel后定向90 passed、kernel69 passed，四合同/CLI/新增lint通过。独立review及scoped re-review均通过，所有发现关闭。

### Task 4：完成与回放一起解耦 sessions

**Files:** Modify `shopping_session/{schemas,service}.py`、`commerce_orchestrator/service.py`、`tests/test_shopping_session_mysql.py`、`tests/test_commerce_turns_mysql.py`；Create `tests/test_turn_payload_validation.py`。

**Interfaces:** 消费 C2 Turn DTO；产出 C2 的完成/回放 JSON 封装。sessions 不理解完整 TurnResponse。

- [x] **Step 1：写失败测试。** `test_completion_envelope_must_match_payload_and_claim` 拒绝三个来源的 session/turn 任一不一致；`test_application_rejects_invalid_response_before_save` 拒绝 status='paid'；`test_existing_json_replays_without_wrapper_change` 从原 response_json 形状回放；非法非 JSON 值不能落库。

```python
def test_invalid_status_is_not_a_valid_turn_response():
    with pytest.raises(ValidationError):
        TurnResponse(request_id='r', session_id='s', turn_id='t', intent='shop', status='paid')
```

该单条 schema 测试只是输入 oracle；本步骤同时新增 application 持久化 spy，断言上述无效 payload 不调用 complete_turn。

- [x] **Step 2：Run `python -m pytest tests/test_turn_payload_validation.py tests/test_shopping_session_mysql.py -q`，Expected：新 envelope/回放安全测试失败；MySQL 夹具不得跳过。**
- [x] **Step 3：按 C2 修改完成与 claim 回放。** application 先校验完整 DTO、归属和 JSON，sessions 再校验 envelope 与 payload 两字段/可序列化性。数据库仍存原 payload，不包新 envelope，不新增列。application 回放时重新校验 DTO 和归属；非法历史响应不能伪造成功。
- [x] **Step 4：Run `python -m pytest tests/test_turn_payload_validation.py tests/test_shopping_session_mysql.py tests/test_commerce_turns_mysql.py tests/test_commerce_graph.py -q`，Expected：原样回放、重复 turn hash、过期租约/接管、version/seen 原子保存通过。**
- [x] **Step 5：检查 sessions 不再引用 models.commerce / commerce_orchestrator，并审核 JSON 基线。** 建议标题 `refactor: decouple session completion and replay payloads`。

完成证据：首次340 full passed；原始JSON补强及review两缺口修后60 focused+11 stage passed、零skip、四合同不变、新lint0；scoped re-review两项ADDRESSED。最终代码全套留后续验收，不冒充首次结果。

### Task 5：事实读取门面，保留锁定与非锁定的既有差异

**Files:** Create `sale_facts/__init__.py`、`sale_facts/service.py`、`kernel/errors.py`、`tests/test_sale_facts.py`；Modify `commerce_orchestrator/facts.py`、`cart/service.py`、`api/runtime.py`、`api/storefront.py`、`api/commerce.py`、`storefront/service.py`、相关事实/购物车测试构造点。

**Interfaces:** 消费 C1/C3 基础服务；产出 C3 SaleFactsReader 和 CommerceFactsReader 新构造。此时 CartService 仍临时保留 sessions 参数，只把 facts 类型换成 SaleFactsReader；Task 9 才移除 sessions。

- [x] **Step 1：写 reader 的失败测试。** 本文件定义 `reader_case` 夹具：本地三源桩、事件列表、事务对象、SKU/CNY 价格/库存 DTO；源码服务不用运行真实网络。覆盖去重/空输入、缺失 ID、明确依赖错误和非依赖错误。

```python
def test_locked_reader_uses_one_session_and_order(reader_case):
    asyncio.run(reader_case.reader.lock_quotes(reader_case.db, ['sku-1']))
    assert reader_case.events == ['catalog', 'pricing', 'inventory']
    assert all(session is reader_case.db for session in reader_case.sessions)
    assert reader_case.commits == 0
```

- [x] **Step 2：Run `python -m pytest tests/test_sale_facts.py -q`，Expected：SaleFactsReader 尚未实现。**
- [x] **Step 3：搬 quote_many/lock_quotes 到门面。** 不新增表，按 C3 返回 DTO；依赖错误归 kernel.errors，服务端只捕获设计列出的异常，不用 except Exception。cart 的 lock_quotes 暂作薄委托，不再访问 facts.catalog；application 的 scan 组合 catalog+reader。单 SKU API 保持“catalog → inventory → pricing”的独立顺序和原错误字符串，不能为复用门面改变原错误优先级。
- [x] **Step 4：Run `python -m pytest tests/test_sale_facts.py tests/test_commerce_facts.py tests/test_commerce_facts_mysql.py tests/test_cart_mysql.py tests/test_commerce_read_api.py tests/test_storefront.py -q`，Expected：包括 price/stock 同时故障时单 SKU 原优先级在内的合同通过。**
- [x] **Step 5：审核所有 reader 构造点并更新业务不得依赖旧应用事实读取的阶段契约。** 建议标题 `refactor: introduce reusable sale facts reader`。

完成证据：SaleFactsReader 已迁入模块；393 passed/零跳过，四合同一致、CLI/JS、新lint0。独立review双批准无发现；完整套件补足Task4最终JSON代码验收。

### Task 6：用 catalog 存在性端口消除 pricing/inventory 的 ORM 越界

**Files:** Create `catalog/ports.py`、`tests/test_catalog_sku_port.py`；Modify `catalog/service.py`、`pricing/service.py`、`inventory/service.py`、`api/runtime.py`、所有两个服务的 seed/API/工具/测试构造点。

**Interfaces:** 产出 C3 SKUExistenceReader/CatalogSKUReader 和两个必需注入的构造函数；PriceReader 原签名搬 ports。

- [x] **Step 1：写失败测试。** `test_missing_sku_maps_to_module_not_found` 分别断言 PricingNotFoundError/InventoryNotFoundError；`test_existence_check_reuses_callers_session` 记录传入 session；`test_catalog_reader_constructs_without_pricing` 验证构造无环。

```python
def test_catalog_reader_constructs_without_pricing(factory_stub):
    reader = CatalogSKUReader(factory_stub)
    assert isinstance(reader, CatalogSKUReader)
```

`factory_stub` 本文件定义为不执行 I/O 的工厂桩；另两条测试的端口 spy 本文件定义，记录 session 并抛 CatalogNotFoundError。

- [x] **Step 2：Run `python -m pytest tests/test_catalog_sku_port.py -q`，Expected：端口/实现未存在。**
- [x] **Step 3：实现公开存在性查询。** 仅 catalog 读取 SKU 模型，无额外锁/commit；两个消费者改用端口，翻译为自己的错误类型；保留 FK 和原提交冲突逻辑。组合根先构造 CatalogSKUReader → pricing/inventory → catalog(price_reader=pricing)，禁止默认内部构造/回填。
- [x] **Step 4：Run `python -m pytest tests/test_catalog_sku_port.py tests/test_catalog_mysql.py tests/test_pricing_mysql.py tests/test_inventory_mysql.py tests/test_import_amazon_metadata.py -q`，Expected：真实缺失 SKU/FK/幂等/并发写入通过。**
- [x] **Step 5：检索函数内 import 和全部构造点并审核。** 禁止用字符串 SQL 绕过 catalog ownership。建议标题 `refactor: inject catalog SKU existence port`。

完成证据：28构造点显式注入，同db SKU端口；408 passed/零skip、四合同/CLI/JS/newlint通过，独立双批准。

### Task 7：先新增 sessions/cart 无提交公共操作与 DTO

**Files:** Modify `shopping_session/{schemas,service}.py`、`cart/{schemas,service}.py`；Create `tests/test_cart_transaction_api.py`、`tests/test_cart_transactions_mysql.py`。

**Interfaces:** 消费 C2/C3；产出 C2 lock_owned_session/has_seen_sku 与 C4 全部 DTO、新增事务接口和纯入口。此时 CartService 构造器暂保留 Task 5 的 sessions 参数，已有 get_action 的返回类型暂不切换；Task 9 连同全部读调用方一起改为 C4 最终构造和 PendingActionView。现有端到端入口仍保持可用。

- [x] **Step 1：写 DTO/事务/回滚的失败测试。** 本文件 `transaction_case` 使用现有 seed 和独立 MySQL factory 构造两个主体/同主体两个会话、报价 100 分库存充足的 SKU，返回 `factory, principal, sessions, cart, command, intent` 具名对象，不再扩展已有位置 tuple。`test_public_snapshot_contains_no_orm` 递归拒绝 SQLAlchemy mapped instance；`test_seen_check_is_in_callers_transaction` 验证 session identity。

```python
def test_proposal_rolls_back_with_outer_transaction(transaction_case):
    async def scenario():
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with transaction_case.factory() as db, db.begin():
                await transaction_case.cart.propose_action_in_transaction(
                    db, transaction_case.principal, transaction_case.session_id,
                    transaction_case.command, transaction_case.intent)
                raise RuntimeError('rollback-probe')
        assert await transaction_case.count_pending_actions() == 0
    asyncio.run(scenario())
```

count_pending_actions 是本文件夹具的只读测试探针，可读取模型；生产跨模块代码不可使用它。

- [x] **Step 2：Run `python -m pytest tests/test_cart_transaction_api.py tests/test_cart_transactions_mysql.py -q`，Expected：新增公共能力未实现；MySQL 零跳过。**
- [x] **Step 3：按 C4 拆内部 ORM helper 与公开方法。** helper 留在所属模块；返回 DTO，不向 checkout/application 暴露对象。find_source/confirmation_replay 的条件、过期/重放优先级、约束和唯一键与原实现一致。所有 db 方法不另开 session/commit/rollback；内部用例共享同一实现，不保留第二份核心算法。
- [x] **Step 4：Run `python -m pytest tests/test_cart_transaction_api.py tests/test_cart_transactions_mysql.py tests/test_cart_confirmation_mysql.py tests/test_shopping_session_mysql.py -q`，Expected：单主体单车、单会话单待决动作、外层回滚与快照不可穿透通过。**
- [x] **Step 5：逐项核对 C4 公共入口与无 ORM 输出，审核锁语义。** 只新增公共能力而未切 caller 是本任务的可审查交付。建议标题 `refactor: expose cart transaction operations through DTOs`。

完成证据：C4无提交公共能力/DTO、回滚/嵌套隔离已建立；旧full438，最终fix1定向105 passed/零skip，四合同/CLI/JS/newlint通过。I1独立scoped re-review关闭；Task8全库将补最终代码完整验证。

### Task 8：checkout 改用 cart 公共能力，并拆出三段执行协议

**Files:** Create `checkout/ports.py`、`tests/test_checkout_transaction_api.py`；Modify `checkout/{schemas,service,gateway}.py`、`tests/test_checkout_mysql.py`。

**Interfaces:** 消费 C4；产出 C5 HandoffAttempt、checkout 无提交方法与 HostCheckoutGateway。此时 CheckoutService 构造器暂保留现有 sessions 参数；现有外层入口内部委托新方法，Task 10 移外层协调并采用 C5 最终构造。

- [x] **Step 1：写失败测试。** `checkout_case` 在本文件基于 Task 7 的原始 seed 构造 ready cart、计数网关与 transaction probes；`test_preparing_and_action_consumption_roll_back_together`、`test_checkout_returns_no_cart_orm`、`test_destination_call_requires_no_open_transaction`、`test_retry_keeps_handoff_id`。

```python
def test_retry_keeps_handoff_id(checkout_case):
    first, recovered = asyncio.run(checkout_case.timeout_then_retry())
    assert first.status == 'unavailable'
    assert recovered.status == 'handoff'
    assert checkout_case.gateway_ids == [recovered.handoff_id, recovered.handoff_id]
```

timeout_then_retry 是该测试夹具的场景函数，先抛一次 TimeoutError，再返回合法 demo Destination；它调用本任务公开接口，不重写生产状态机。

- [x] **Step 2：Run `python -m pytest tests/test_checkout_transaction_api.py -q`，Expected：新准备/收尾接口或 DTO 边界未实现。**
- [x] **Step 3：移除 cart.models 引用。** 用 lock_cart_snapshot/lock_pending_action/回执公开方法替换；preview 的 created_at/expires/fee_boundary 不变，snapshot 从 DTO.summary 读取。prepare 只在 caller 事务写 preparing + 消费动作；request_destination 保留已有 timeout/白名单/失败语义；finalize 在独立 caller 事务保存 ready/failed。get_owned 用 find_action_owner 核对 session，避免 JOIN 其他模块的表。
- [x] **Step 4：Run `python -m pytest tests/test_checkout_transaction_api.py tests/test_checkout_mysql.py tests/test_checkout_api.py -q`，Expected：空车、无宿主 503、变价重确认、失败恢复、ready 回放及非法 URL 均保持原行为。**
- [x] **Step 5：审核网络调用前后的事务边界及所有 cart 内部访问。** 旧协调方法暂时仅委托，禁止复制状态机。建议标题 `refactor: expose checkout preparation and finalization protocol`。

完成证据：Checkout C5三段公共协议/同事务消费，无cart ORM/private引用；463 passed/零skip、四合同/CLI/JS/newlint通过，独立双批准无发现。Task7最终deepcopy回归在本次全库通过。

### Task 9：application 接管购物车会话/轮次门禁

**Files:** Create `commerce_orchestrator/actions.py`、`tests/test_cart_actions_mysql.py`；Modify `cart/service.py`、`commerce_orchestrator/{nodes,service,graph}.py`、`api/commerce.py`、`api/runtime.py`、购物车/确认/e2e 相关测试。

**Interfaces:** 消费 C2–C4；产出 C5 CartActionService 的 cart 三类入口与读动作入口。CartService 最终构造按 C4，不再 sessions/TurnClaim。

- [x] **Step 1：写事务门禁失败测试。** `test_lease_takeover_blocks_proposal_and_confirmation`、`test_direct_api_checks_session_without_source_turn`、`test_same_principal_sessions_share_cart_version`、`test_committed_confirmation_retries_once`；每个测试使用本文件的具名场景夹具，以现有 seed 创建 100 分 SKU。

```python
def test_committed_confirmation_retries_once(cart_action_case):
    async def scenario():
        replies = await cart_action_case.confirm_four_times_same_key()
        view = await cart_action_case.read_cart()
        assert all(reply.status == 'cart_updated' for reply in replies)
        assert view.version == 2
        assert view.items[0].quantity == 2
        assert view.current_subtotal == 200
    asyncio.run(scenario())
```

确认四次在同一个 asyncio 场景内并发，各自使用独立 AsyncSession；同一循环中再只读核验并 dispose 本场景拥有的 engine，避免连接池跨循环复用。

- [x] **Step 2：Run `python -m pytest tests/test_cart_actions_mysql.py -q`，Expected：application 动作用例尚未实现或旧模块仍承担门禁。**
- [x] **Step 3：实现事务所有权并同提交切换全部入口。** propose/confirm 在同一个 db.begin 内锁归属、校验租约/seen、调 cart 公共操作；source_turn_id 只作相关值传入。图节点和聊天确认/直接 confirm API 调用 actions，不再调用 CartService 的旧 source_turn 方法。get_action 改为 PendingActionView，同时调整读调用方从 action_id/payload/summary 取值。迁出 CartService 中 ShoppingSessionService/SeenSKU/TurnClaim，移除旧模块协调入口而不是留安全旁路。
- [x] **Step 4：Run `python -m pytest tests/test_cart_actions_mysql.py tests/test_cart_confirmation_mysql.py tests/test_cart_confirmation_api.py tests/test_commerce_chat_confirmation.py tests/test_commerce_e2e.py -q`，Expected：拒绝不写车、丢租约不写动作、版本冲突/重复确认/断线重试/变价重确认通过，MySQL 零跳过。**
- [x] **Step 5：审核完整入口调用图与锁顺序。** 未涉及 checkout 的 source_turn 迁移延至 Task 10，不能提前启用“checkout 不认识轮次”的全量断言。建议标题 `refactor: coordinate cart actions in application transactions`。

完成证据：CartActionService同事务门禁/全部入口切换、Cart无session/TurnClaim；最终469 passed/零skip，四合同/CLI/JS/newlint通过，独立双批准。CommerceError归kernel提前仅移位置，HTTP字段Task11再处理。

### Task 10：application 接管结账确认与所有权读取

**Files:** Modify `commerce_orchestrator/actions.py`、`checkout/service.py`、`commerce_orchestrator/{nodes,service,graph}.py`、`api/{commerce,commerce_demo,runtime}.py`；Create `tests/test_checkout_actions_mysql.py`；Modify 结账/API/demo/e2e 测试调用点。

**Interfaces:** 消费 Task 8 的 C5 协议；产出完整 C5 CartActionService 的 preview_checkout/confirm_handoff/get_handoff。CheckoutService 最终不持 sessions/TurnClaim。

- [x] **Step 1：写失败测试。** `test_handoff_source_lease_checked_before_preparing`、`test_network_runs_after_prepare_commit`、`test_same_key_retry_after_response_loss_reuses_ready_result`、`test_handoff_lookup_rejects_other_session_or_principal`；场景夹具沿用 Task 8 的计数网关/原始 seed，并注入 application coordinator。

```python
def test_network_runs_after_prepare_commit(checkout_action_case):
    asyncio.run(checkout_action_case.confirm())
    assert checkout_action_case.gateway_observed_preparing is True
    assert checkout_action_case.gateway_observed_consumed is True
    assert checkout_action_case.open_transaction_at_gateway is False
```

网关探针从独立连接读已提交状态；测试不把注入网关的断言吞掉为业务超时，因此在场景结束后检查记录值。

- [x] **Step 2：Run `python -m pytest tests/test_checkout_actions_mysql.py -q`，Expected：门禁/事务协调尚未迁移。**
- [x] **Step 3：按 prepare → 锁外 request → finalize 编排。** 第一事务内有同 source_turn/主体/会话门禁及动作回放；prepare 返回 HandoffResult 时不调用网络。第二事务不新增原来没有的租约重检；不改变当前网络交接后失租约的语义。get_handoff 经 sessions 检查与 checkout 所有权查询；demo 读取同样调用 application，不绕过门禁。
- [x] **Step 4：Run `python -m pytest tests/test_checkout_actions_mysql.py tests/test_checkout_mysql.py tests/test_checkout_api.py tests/test_commerce_chat_confirmation.py tests/test_commerce_demo.py tests/test_commerce_e2e.py -q`；Run `node tests/run_demo_contracts.cjs`，Expected：关键集成零跳过，宿主失败/同 ID 恢复/非法目标、聊天和直接 API 的响应不变。**
- [x] **Step 5：审核 checkout 的原协调入口已删除与图依赖改为 actions。** 建议标题 `refactor: coordinate checkout handoff in application`。

完成证据：完整CartActionService结账门禁/三段编排，全读写入口切换；482 passed/零skip、四合同/CLI/JS/newlint通过，独立双批准无发现。固定HTML demo无记录读取，无需无关改动。

### Task 11：共享流程异常与兼容 HTTP 映射，迁出 HTTP 配置

**Files:** Create `api/errors.py`、`api/http_config.py`、`tests/test_error_mapping_contracts.py`；Modify `kernel/errors.py`、`shopping_session/service.py`、`identity/types.py`、`config/settings.py`、`api/{identity,auth,catalog,commerce,commerce_read,storefront,runtime}.py`、所有 CommerceError 构造/捕获处与业务异常基类。

**Interfaces:** 产出 C1 最终 CommerceError；`api.errors.commerce_http_error(exc: DomainError) -> HTTPException`、`catalog_http_error(exc: CatalogError) -> HTTPException`，保留 endpoint 特殊映射；IdentityHttpConfig/parse_trusted_networks 归 api/http_config，identity 只接收业务配置值。

- [x] **Step 1：写错误合同失败测试。** 从当前 raises 和 response tests 枚举 code → status/detail；包含动态 quote.reason 错误码、not_found、turn_processing、checkout_unavailable、身份 no-store/Retry-After/Cookie 清理。不得把现有测试改为接受新错误形状。

```python
def test_commerce_error_does_not_carry_http_status():
    error = CommerceError('cart_version_conflict', {'version': 2})
    assert not hasattr(error, 'status_code')
    response = commerce_http_error(error)
    assert response.status_code == 409
    assert response.detail == {'code': 'cart_version_conflict', 'version': 2}
```

- [x] **Step 2：Run `python -m pytest tests/test_error_mapping_contracts.py -q`，Expected：新语义构造/映射未实现。**
- [x] **Step 3：迁异常与配置。** 根 DomainError 无业务字段，CommerceError 移 kernel；application 将 status_code 判断换成 code/type；API 保留 catalog 字符串、commerce 字典、单 SKU 各源故障字符串及 identity 响应副作用。部署常量只定义一次，配置读取由组合根承担。未知异常返回既有内部错误策略，不回显 SQL/秘密，不把所有 DomainError 归 422。
- [x] **Step 4：Run `python -m pytest tests/test_error_mapping_contracts.py tests/test_api_routes.py tests/test_commerce_read_api.py tests/test_auth_api.py tests/test_commerce_auth_api.py tests/test_storefront_api.py tests/test_layering_baseline.py -q`，Expected：status/detail/headers/OpenAPI 合同不变，内层不再出现 HTTP status。**
- [x] **Step 5：审核所有错误码登记完整性和未知错误行为，检查 HTTP 配置没有引起 api/application 导入环。** 建议标题 `refactor: centralize compatible HTTP error mappings`。

完成证据：26错误码/54构造去HTTP、HTTP配置归API，556 passed/零skip、四合同/CLI/JS/newlint通过。独立Spec compliant / Approved，报告依赖统计Minor交实现者更正。

### Task 12：完整构造图与 Runtime，资源生命周期显式归属

**Files:** Create `commerce_orchestrator/{ports,guards,llm_adapter}.py`、`tests/test_runtime_composition.py`；Modify `commerce_orchestrator/{model,graph,service}.py`、`db/session.py`、`api/runtime.py`、`main.py`、现有构造桩测试。

**Interfaces:** 消费 C1–C5；产出 C6 图/Runtime/DatabaseResources。model.py 临时对原消费者重导出同一对象；真实 LLM 实现暂在 llm_adapter，Task 14 再归 infra。

- [x] **Step 1：写构造/关闭/无配置失败测试。** `test_runtime_uses_one_actions_cart_checkout_and_model`、`test_graph_compiles_once_before_first_request`、`test_owned_resources_close_once_on_failure_and_shutdown`、`test_injected_resources_are_not_closed`、`test_missing_database_preserves_health_and_503`；本文件注入本地模型、网关、工厂与有计数器的资源桩。

```python
def test_owned_resources_close_once_on_shutdown(runtime_case):
    asyncio.run(runtime_case.close_twice())
    assert runtime_case.redis_close_calls == 1
    assert runtime_case.engine_dispose_calls == 1
```

runtime_case 是本文件夹具，资源方法只记次数，close_twice 在一个事件循环中调用两次同一 Runtime.aclose。

- [x] **Step 2：Run `python -m pytest tests/test_runtime_composition.py -q`，Expected：新构造协议或完整注入/ownership 尚未存在。**
- [x] **Step 3：按设计装配顺序构造一次。** 分开模型端口/guards/LLM，构造 CatalogSKUReader → pricing/inventory/catalog → facts/sessions/cart/checkout/events → actions → discovery/comparison → deps/graph → orchestrator。Runtime 使用完整构造值，不用 commerce.cart/checkout/intent_model/reranker 回填，不在首请求 configure_graph。按 C6 用异步资源 guard 覆盖返回前的失败清理；main 的 lifespan await create_runtime 后才发布 app.state.runtime，并在 finally 中关闭。此时仍保留 main:app 入口，Task 15 才切工厂；修改测试构造调用以 await 同一入口，不在 async 路径里 asyncio.run。
- [x] **Step 4：Run `python -m pytest tests/test_runtime_composition.py tests/test_commerce_graph.py tests/test_commerce_model.py tests/test_commerce_chat_confirmation.py tests/test_api_routes.py tests/test_layering_baseline.py -q`，Expected：无真实 LLM调用，首轮/后续轮次走相同图，默认降级/缺配置响应不变。**
- [x] **Step 5：审核 legacy 装配保持原行为。** 旧 rec_graph 与实验实例分裂不顺便修复；只调整它们引用路径。建议标题 `refactor: construct complete commerce runtime dependencies`。

完成证据：C6异步完整Runtime/Graph与owned资源guard，纯目的地校验归checkout；592 passed/零skip、四合同/CLI/JS/newlint通过，独立双批准无阻塞。旧legacy实例分裂保留，第一轮夹具失败另存。

### Task 13：补齐阶段架构契约与 AST 正负例，关闭第一阶段

**Files:** Create `tests/layering_rules.py`、`tests/test_layering_rules.py`；Modify `.importlinter.stage.toml`、`tests/test_layering.py`、`pyproject.toml`、`.github/workflows/layering.yml`、所有明确遗漏的公共入口 `__all__`（只改导出，不重写实现）。

**Interfaces:** 消费 C6 架构工具签名；产出 original 阶段完整已修复契约。Task 2 以来的 shared/kernel 临时检查合并进这一个入口，不撤销约束。

- [x] **Step 1：写白名单失败测试。** 临时目录 fixture 建小型包图；允许跨模块 service → 自己 models 的间接依赖；拒绝直接外部 models、私有符号/ORM 重导出、kernel.service、application.api、route.infra、相对/函数内/TYPE_CHECKING 绕过和新模块漏登。

```python
def test_indirect_model_use_is_allowed_but_direct_use_is_not(layout_case):
    assert check_import_boundaries(layout_case.legal, 'original') == []
    violations = check_import_boundaries(layout_case.direct_foreign_model, 'original')
    assert any('catalog.models' in violation for violation in violations)
```

layout_case 本文件定义为临时路径集合，内部源文件只含 import/class 结构，不导入真实 infra。生成器也用同一模块清单拒绝遗漏。

- [x] **Step 2：Run `python -m pytest tests/test_layering_rules.py -q`，Expected：检查器未实现。**
- [x] **Step 3：实现并生成 original 配置。** 枚举实际顶层应用包，包含路径中所有中间节点；source 角色分为业务/用例/API/基础/共享，尚未分目录时用精确文件/包映射。跨模块模型 forbidden 用 allow_indirect_imports=true，上层 forbidden 保留间接检查；SQLAlchemy模型注册/组合根/过渡适配器只用精确例外。AST 校验公共符号/相对引用/包级重导出；禁止动态导入绕过。未搬到 infra 的 db.session、checkout.gateway、llm_adapter 是精确登记的适配器角色，只允许当前 main/api.runtime 构造。
- [x] **Step 4：Run `lint-imports --config .importlinter.stage.toml --no-cache`；Run `python -m pytest tests/test_layering.py tests/test_layering_rules.py tests/test_layering_baseline.py -q`；重跑 Task 1 三类门禁。Expected：真实所有现代业务模块边界通过，关键集成零跳过，原合同和收集清单未丢失。** 缺 CLI/目标包直接失败，不 importorskip，不配 ecom。
- [x] **Step 5：审核并提交第一阶段验收报告。** 报告位置 `docs/superpowers/verification/2026-10-06-layering-phase-one.md`，只记录命令/统计/差异，不含连接 URL。建议标题 `test: enforce pre-move layering boundaries`。**到此暂停，请用户决定 D5/R/L；不得自动继续大范围搬迁。**

任务审查：常见静态别名绕过经两轮补修及复核，整体阶段审查反射函数别名缺口经定点修复及独立复核；最终221定向，控制器714全套与三门禁534/151/29均零跳过，CLI68/JS10/新增lint0、原节点与四冻结合同保留。证据见第一阶段验收报告。未授权git提交，Step5只交付文档，不创建提交；停止于D5。

### Task 14：经 D5 批准后的顶层分组，保留 legacy

**Files:** Move `catalog/pricing/inventory/cart/checkout/identity/shopping_session/commerce_events/sale_facts` → `modules/catalog/pricing/inventory/cart/checkout/identity/sessions/events/sale_facts`；Move `commerce_orchestrator` → `application/shopping`（service → orchestrator），storefront → application/storefront；Move `api/runtime.py` → `api/deps.py`、API 路由 → `api/routes/`、HTTP 输入 → `api/contracts/`；Create `infra/{db,redis,llm,checkout_gateway}.py` 与 groups 的空 __init__、`api/middleware.py`、`tests/test_layering_layout.py`；Modify `main.py`、`migrations/env.py`、原测试导入、阶段配置/AST、`AGENTS.md`、README、`docs/architecture.md`。

**Interfaces:** C1–C6 不改签名，只改命名空间；grouped 布局名称没有 ecom 前缀。D1保留旧推荐栈为legacy、D2保留实验与指标已确认；旧API路径字符串、注册、输出与测试保持不变，不触发Task19。

- [x] **Step 1：确认 D5 记录并写迁移失败测试。**（偏差：`tests/test_layering_layout.py` 在搬迁**之后**补齐，未遵守先红后绿顺序；断言本身与 grouped 布局一一对应。） `test_grouped_imports_and_public_entries`、`test_application_has_no_api_or_infra_dependency`、`test_legacy_routes_are_still_registered`，使用基线比较而非改期望；测试文件 `tests/test_layering_layout.py`。

```python
def test_grouped_imports_and_public_entries():
    from application.shopping.contracts import TurnResponse
    from modules.cart.service import CartService
    assert TurnResponse.__name__ == 'TurnResponse'
    assert CartService.__name__ == 'CartService'
```

- [x] **Step 2：Run `python -m pytest tests/test_layering_layout.py -q`，Expected：grouped 新路径未存在导致预期红灯。**
- [x] **Step 3：执行现代文件的机械搬迁并调整 import。** infra.db 承接 db.session 的资源实现，infra.redis 承接 Redis 构造，infra.llm 承接 llm_adapter，infra.checkout_gateway 承接两种实现，校验函数仍在 modules.checkout.service。API 精确按设计 §5.3 映射（此时去掉 ecom 前缀）：catalog+commerce_read → routes/catalog.py，commerce_demo → routes/demo.py，auth+identity 的路由能力 → routes/auth.py，identity 的 CORS/校验处理/client_ip → 新 api/middleware.py；auth BaseModel → contracts/auth.py，其余 HTTP 输入 → contracts/commerce.py，Turn 类型从 application 复用。合并时保留各路由原 prefix、schema 与错误顺序，不遗漏 main 对中间件的装配；config 暂保留顶层包。
- [x] **Step 4：按明确位置保留 legacy 并移除空壳。** agents/orchestrator → legacy 同名包；models.schemas → legacy/models；services 的 ab_test/metrics/feature_store → legacy/services；infrastructure → legacy/infrastructure；catalog.legacy_adapter → legacy/catalog_adapter。recommendations/experiments → api/routes/legacy_recommendations/legacy_experiments，metrics 保持 api/routes/metrics。main/deps 和这三个过渡路由之外不得直接引用 legacy，新业务内层更不能间接依赖。移除已无消费者的旧 schemas/utils/model 兼容文件，保留唯一对象，不删旧业务测试。
- [x] **Step 5：更新 grouped 阶段契约/AST及配套文档，Run `lint-imports --config .importlinter.stage.toml --no-cache`、`python -m pytest tests/test_layering_layout.py tests/test_layering.py tests/test_layering_rules.py tests/test_layering_baseline.py -q`，再跑三类门禁。Expected：old route 合同仍在，node ID 一一对应，metadata 不变。**
- [x] **Step 6：整体审核搬迁清单与回滚点。**（独立复核已完成：结论 Spec issues，指出两处**未记录的契约覆盖收缩**——生成器 `visible` 过滤丢弃顶层命名空间包、`protected` 契约丢失 `legacy.infrastructure`。两处均为本次重写引入的回归，已修复并用探针反证；详见验收报告 §7。） docs/architecture 描述新栈与旧栈并存，其余旧栈文档标明适用版本，不提前归档；不整理无关格式。建议标题 `refactor: group backend modules without retiring legacy APIs`。

### Task 15：ecom 唯一包根、factory 启动与迁移入口

**Files:** Move `main.py`、`shared/kernel/modules/application/infra/api/legacy` → `ecom/`；Move `config/settings.py` → `ecom/config.py`；Modify `Dockerfile`、`docker-compose.yml` 的入口（不顺便重写服务配置）、`alembic.ini`、`migrations/env.py`、README、AGENTS、工具/测试 import、pyproject/AST；Create `tests/test_packaged_entrypoints.py`；Remove 阶段配置和已切换的旧导入空壳。

**Interfaces:** 最终 C1–C6 均带 ecom 前缀；`ecom.main.create_app() -> FastAPI`；源码 factory 入口按设计 §6.3，不要求 pip install .。

- [ ] **Step 1：写缺配置/工作目录/metadata 失败测试。** `test_factory_routes_match_original_contract`、`test_factory_lifespan_closes_owned_resources`、`test_import_does_not_build_runtime_or_call_llm`、`test_demo_path_is_independent_of_route_depth`、`test_registered_metadata_matches_pre_move`；用本地依赖桩和 monkeypatch 隔离环境，不触发真实模型。

```python
def test_factory_has_health_route():
    from ecom.main import create_app
    app = create_app()
    assert 'get' in app.openapi()['paths']['/health']
```

- [ ] **Step 2：Run `python -m pytest tests/test_packaged_entrypoints.py -q`，Expected：ecom 工厂尚未存在。**
- [ ] **Step 3：连同入口一次切换命名空间。** 完整迁 import，config 设置值/前缀不改；create_app 注册路由/lifespan，Runtime 不在 import 时创建，lifespan 完成资源装配及关闭。删除 sys.path.insert，demo 位置由组合根按 `Path(__file__).resolve()` 的仓库根配置；路由不猜深度。Docker CMD 使用 `python -m uvicorn ecom.main:create_app --factory --host 0.0.0.0 --port 8000`，源码部署要求从仓库根启动；不宣称未安装的包能从任意 cwd 被导入。
- [ ] **Step 4：切目标契约并更新 Alembic。** pyproject root_packages=[ecom]，移除阶段配置；按设计目标构建 protected + 全模块对直接内部访问 + AST/legacy 检查。migrations/env 更新八模块注册与唯一 Base，alembic.ini 的工具 prepend_sys_path 可保留；迁移历史和 schema 不变。测试与脚本所有 get_settings 的 import 同步。
- [ ] **Step 5：验证 factory、真实迁移和合同。** Run `lint-imports --config pyproject.toml --no-cache`；Run `python -m pytest tests/test_packaged_entrypoints.py tests/test_layering.py tests/test_layering_rules.py tests/test_layering_baseline.py tests/test_catalog_mysql.py -q`；Run `python -m alembic heads`（Expected：仍一个 `0008_commerce_events` 头）；在测试库通过测试夹具执行新库 upgrade/旧版本 upgrade/downgrade 与无意外 metadata diff。启动 smoke test 用禁用模型的配置，`GET /health` 为原形状，未配置数据库端点仍原 503。
- [ ] **Step 6：审核回滚包。** 回滚需同时恢复导入、Docker/Alembic/静态/契约；不做数据回迁。建议标题 `refactor: consolidate backend under ecom package root`。

### Task 16：tests 按依赖分类、离线工具与 CI 完整收尾

**Files:** Move 无真实服务测试 → `tests/unit/`，真实 MySQL/Redis 测试 → `tests/integration/`，可复用测试辅助 → `tests/support/`；Keep `tests/conftest.py`；Move `data/{download,import}_amazon_metadata.py`、`data/{requirements,import-requirements}.txt`、`data/{README,IMPORT}.md` → `tools/amazon/`；Move `scripts/reset_demo_password.py` → `tools/`；Move 基线校验/架构工具 → tests/support；Create `tests/test_test_layout_contract.py`、`tools/__init__.py`、`tools/amazon/__init__.py`；Modify fixture 导入、CI/runner、README/工具文档、AGENTS 与报告映射。

**Interfaces:** 架构/报告接口签名不变，只改 tests.support 归属；`python -m tools.amazon.download_amazon_metadata --help`、`python -m tools.amazon.import_amazon_metadata --help`、`python -m tools.reset_demo_password --help`，不删除已有下载数据。

- [ ] **Step 1：写收集/入口失败测试。** `tests/test_test_layout_contract.py::test_every_original_node_has_a_mapped_successor` 比较 Task 1 manifest 与新的显式路径映射；`test_tool_help_does_not_access_network_or_database` 子进程 --help；`test_fixture_scope_and_root_paths_survive_move` 固定根 conftest 的 REPO_ROOT。

```python
def test_fixture_root_does_not_change():
    from tests.conftest import REPO_ROOT
    assert (REPO_ROOT / 'alembic.ini').is_file()
    assert (REPO_ROOT / 'ecom').is_dir()
```

同时添加新 unit/integration import 的断言制造迁移前红灯；单独这条兼容断言不必失败。

- [ ] **Step 2：Run `python -m pytest tests/test_test_layout_contract.py -q`，Expected：新布局/工具尚未存在，原 root oracle 仍成立。**
- [ ] **Step 3：按 fixture 真实依赖分类并移动测试辅助。** 混合文件如 identity_rate_limit 拆纯桩与 Redis 用例，不删除任何测试；不迁根 conftest，不复制夹具。原测试间的 setup/seed 辅助从 test_* 文件提到 support，签名用具名结构替换前述 tuple 的改动需与消费者同提交。更新 AST 文件到角色映射与 linter pytest wrapper 的 root parents[2]。报告 CLI 同步改为 `python -m tests.support.layering_reports`，CI 的 lint 基线/路径映射同步，不重生成基线掩盖新告警。
- [ ] **Step 4：迁工具代码/文档但不动数据。** 执行前记录 data 中代码、README 与数据目录清单；仅移动明确源码/说明/独立 requirements，不下载 27 GB 数据、不导入真实库。默认缓存/输出目录保持原 data 位置并由显式 repo_root 计算，不能因 __file__ 深度变化悄悄改变；旧 README/CLI 若需兼容则薄入口只委托同一实现，记录删除条件。
- [ ] **Step 5：Run `python -m pytest tests/ --collect-only -q --layering-artifacts=.artifacts/layering/final`，再跑三类 CI、报告比较与 `node tests/run_demo_contracts.cjs`；运行三个 tools --help 子进程。Expected：旧节点映射无丢失、集成零跳过、JS 合同通过、--help 无网络/DB副作用。** Node runner 仍使用仓库根，demo JS 测试保持原入口；前端无需改动，但执行现有 typecheck/test 作为无回归佐证时不安装新框架。
- [ ] **Step 6：完成结构阶段报告并审核。** `docs/superpowers/verification/2026-10-06-layering-structure.md` 对照设计 P1–P5、8 项评审问题、所有 D 决策状态和回滚清单，不宣称 R/L 已完成。建议标题 `refactor: organize backend tests and offline tools`。

### Task 17：独立 R 提交——统一可购买性规则

**Files:** 默认在 Task 13 后、Task 14 前 Modify `kernel/quotes.py`、`sale_facts/service.py`、`api/commerce_read.py`、`cart/service.py`、`tests/{test_kernel_contracts,test_sale_facts,test_commerce_read_api,test_cart_mysql,test_checkout_mysql}.py`；新增差异记录 `docs/superpowers/verification/2026-10-06-layering-rule-changes.md`。若延期至 Task 15 后，则源码按 Task 14/15 映射，测试命令按 Task 16 的 node ID 路径清单生成，不能保留已失效的顶层测试路径。

**Interfaces:** 最终所有调用方仅消费 C1 evaluate_purchasability；删除两个 legacy 包装。允许 R 在 Task 13 之后、D5 之前执行；先固定实际布局并只修改对应一套路径。

- [ ] **Step 1：获得并记录 R 逐项审批。** 变化包含 API 的非 CNY → price_missing、cart 锁定路径的非 available 状态/负数 → out_of_stock；非锁 facts 已有负数防御。不得改变缺 SKU HTTP 404、数量上限或单 SKU 故障查询顺序。
- [ ] **Step 2：写跨路径一致性红灯。** 测试 fixture `quote_rule_case` 在当前布局的 kernel 测试文件中分别调用纯规则、普通 facts、锁定 facts、报价 API；参数包括 sku None/inactive/product draft、price None/USD、stock unknown/None/0/-1/out_of_stock+正数、多个故障。

```python
def test_negative_stock_is_never_purchasable(quote_rule_case):
    results = quote_rule_case.evaluate_all_paths(quantity=-1, status='available')
    assert all(result.reason == 'out_of_stock' for result in results)
    assert all(result.purchasable is False for result in results)
```

- [ ] **Step 3：Run `python -m pytest tests/test_kernel_contracts.py tests/test_sale_facts.py tests/test_commerce_read_api.py -q`，Expected：只因 legacy 较弱路径不一致红灯。** 不把该红灯与结构提交混在一起。
- [ ] **Step 4：切换所有路径到唯一函数并删除包装。** 不增加 AvailabilityView 的输入约束；保留 SKU → 商品 → 价格 → 库存状态 → 数量的优先级与七个 reason。
- [ ] **Step 5：Run `python -m pytest tests/test_kernel_contracts.py tests/test_sale_facts.py tests/test_commerce_read_api.py tests/test_cart_mysql.py tests/test_checkout_mysql.py tests/test_layering_baseline.py -q`，再跑 Task 1 三类门禁；Expected：结构合同只有批准的 reason 差异。** 报告比较 allowed_changes 必须逐项含审批编号；rollback 恢复这些旧差异。审核后建议标题 `fix: unify purchasability checks across quote paths`。

### Task 18：独立 R 提交——统一商品条件和现价预算判断

**Files:** 默认在 Task 13 后、Task 14 前 Modify `kernel/constraints.py`、`commerce_orchestrator/facts.py`、`cart/service.py`、`tests/{test_kernel_contracts,test_commerce_discovery,test_commerce_facts,test_cart_transactions_mysql}.py`；补充 Task 17 的差异记录。Task 15 后对应 `ecom/kernel/constraints.py`、`ecom/application/shopping/facts.py`、`ecom/modules/cart/service.py`，测试命令同样依照 Task 16 的路径映射。

**Interfaces:** 消费 C1 的两个纯条件函数、C3 SaleFactView 与 C4 intent 参数；不创建从 product 猜价格的第三个函数。

- [ ] **Step 1：确认预算/条件差异审批。** 大部分现有类目/硬条件逻辑相同，提取部分不得宣称新增行为；非 CNY/缺价格按设计最终规则失败，若已被 Task 17 可买门禁拒绝，不重复计算新差异。
- [ ] **Step 2：写扫描与锁定输入一致性红灯。** `test_budget_is_inclusive_and_uses_current_price`、`test_bool_does_not_match_integer_attribute`、`test_inferred_condition_is_not_hard_filter`、`test_unfilterable_or_missing_attribute_fails`；同一 product/attribute/price 用 ProductDetailView 与 SaleFactView 分别验证。

```python
def test_budget_is_inclusive_and_uses_current_price(constraint_case):
    intent = ShoppingIntent(budget_max_minor=100)
    assert matches_budget(intent, constraint_case.price(amount_minor=100))
    assert not matches_budget(intent, constraint_case.price(amount_minor=101))
    assert not matches_budget(intent, None)
```

constraint_case.price 在该测试文件创建具合法 SKU/version/UTC 的 PriceView，参数只覆盖 amount_minor。

- [ ] **Step 3：Run `python -m pytest tests/test_kernel_contracts.py tests/test_commerce_discovery.py tests/test_commerce_facts.py tests/test_cart_transactions_mysql.py -q`，Expected：跨视图复用尚未落实；已有同语义测试可作为兼容绿灯，不能伪造失败。**
- [ ] **Step 4：删除 scan/cart 条件副本。** scan 使用 ProductDetailView + category_attributes，锁定路径使用 SaleFactView.product + category_attributes，两者价格独立传 matches_budget；保留商品 active 检查及严格 type 比较、source='inferred' 不过滤。
- [ ] **Step 5：Run `python -m pytest tests/test_kernel_contracts.py tests/test_commerce_discovery.py tests/test_commerce_facts.py tests/test_cart_transactions_mysql.py tests/test_cart_confirmation_mysql.py tests/test_commerce_graph.py tests/test_layering_baseline.py -q`，再跑三类门禁。Expected：相同约束两条路径结果一致，预算为整数分/包含等号，批准之外响应不变。** 审核后建议标题 `refactor: centralize product and price constraints`。

### Task 19：旧推荐栈/死代码退役（本轮不执行）

**状态：已排除本轮执行，不是已完成退役。** 用户已确认D1保留旧推荐栈为legacy、D2保留实验与指标；Task14–16必须走保留分支，旧接口、注册、依赖、消费者及测试保持不变。下面步骤只作为未来专项退役的参考，不创建退役fixture、不删除测试或依赖，不执行本任务任何代码步骤。未来改变保留决定须重新审批明确的路径/路由/消费者清单，不能仅因D1/D2已有确认记录就启动本任务。

**Files:** Create `tests/unit/test_legacy_retirement_contracts.py`（未完成 Task 16 则为 `tests/test_legacy_retirement_contracts.py`）、`tests/fixtures/layering/legacy-retirement.json`；仅针对 D1/D2 审批清单删除/调整 `ecom/legacy/`（未搬包则为原旧栈路径）、legacy_recommendations/legacy_experiments/metrics 路由、Runtime/main 注册、对应旧栈测试、requirements 和文档；不能删除现代 models/commerce 的已迁契约或现代 shopping 模块。

**Interfaces:** 当前无退役审批产物。未来另行批准退役时，`docs/superpowers/verification/2026-10-06-layering-retirement.md` 必须逐项列路径、被移除route、消费者处置、测试退出及回滚通知；没有明确的退役批准则整个任务不执行。

- [ ] **Step 1：取得新的退役批准及逐路由消费者记录。** 当前D1/D2已选择保留，本轮跳过本任务全部步骤。未来须先明确修改保留决定，metrics/experiments是否退出也须有独立答案，不因推荐退役自动删除；新购物指标能力的归属须另行设计，不能随意新增experiments分组。
- [ ] **Step 2：按批准清单先新增退役合同测试。** `test_approved_retired_route_is_not_registered` 比较批准 route 集合；`test_unapproved_metrics_route_is_unchanged` 保留仍批准存续的合同；未执行前应只因待退役 route 仍在而失败。

```python
def test_approved_retired_route_is_not_registered(retirement_case):
    registered = {route.path for route in retirement_case.app.routes}
    assert retirement_case.approved_retired_paths.isdisjoint(registered)
    assert retirement_case.required_live_paths <= registered
```

retirement_case 是本文件 fixture，批准集合从审核记录的去敏 JSON fixture 读取，不能从新代码倒推期望。

- [ ] **Step 3：Run `python -m pytest tests/unit/test_legacy_retirement_contracts.py -q`（未完成 Task 16 使用 Files 中的旧路径），确认仅因批准的旧 route 仍在而红灯，再删除批准代码及路由注册。** 先核查剩余调用方再删测试；仅当最后一个消费者消失才移除其依赖包。无生产消费者的 infrastructure/feature_store 清理也需在明确清单内。
- [ ] **Step 4：更新契约/文档与基线。** legacy 尚存在则保留禁止内层依赖及过渡白名单；只删退出的白名单，完全消失后才移除针对不存在包的契约。更新 API 示例/README/旧文档状态与已批准测试退出映射。
- [ ] **Step 5：重跑上一步退役合同命令和 `python -m pytest tests/ -q --layering-artifacts=.artifacts/layering/retirement --junitxml=.artifacts/layering/retirement/results.xml`，再跑三类 CI 与差异比较。Expected：只有已审批 route/test 退出，现代购物/身份/demo 功能无退化。** 整体回滚恢复代码、路由、测试与外部退役通知。建议标题 `refactor: retire approved legacy recommendation endpoints`。

## 全计划验收与自审对照

| 设计要求 | 实施任务与证据 |
| --- | --- |
| P1/P3 六组与唯一包根 | Task 14–16：布局导入/启动/Alembic/tools/静态及 node ID 映射 |
| P2 上下层/跨模块边界 | Task 3–11、13：无 API DTO 反向依赖、无跨模块 ORM、白名单正负例 |
| P4 单一规则归属 | Task 3 抽取且兼容；Task 17–18 才批准并完成行为归一 |
| P5 构造即完整 | Task 12：实例唯一/图只构造一次/失败资源清理与禁止回填 |
| sessions 不透明完成/回放 | Task 4：完整 DTO 前置校验、归属/JSON 双校验、历史原样回放 |
| 同事务租约/seen/版本/确认 | Task 7–10：真实 MySQL rollback/接管/多会话共享车/幂等与网络隔离 |
| 兼容错误体与身份副作用 | Task 1、11：冻结原 API 合同，字符串/字典/headers/Cookie 不变 |
| 原阶段与目标契约不混用 | Task 13/14/15：original → grouped → packaged，缺包/CLI不跳过 |
| 防假绿与安全测试环境 | Task 1/16：完整 node ID/JUnit映射、专用服务零跳过、串行 MySQL |
| 六组之外保留 legacy 受控 | Task14–16按D1/D2保留，只允许组合根/三个过渡路由；Task19本轮不执行 |
| 源码部署，不新增 schema | Task 2/15：唯一 metadata、同迁移头、无 schema diff，无 wheel 宣称 |
| 审批、范围与回滚 | D5已确认，D1/D2已选择保留，R仍待批准；每任务整体检查点，未来退役须重新审批 |

本轮已完成计划自审；以下勾选仅表示文档检查完成，不表示任何实施任务或应用测试已执行。

- [x] 自审：Files 的源路径依据当前源码或前置任务核对；Create 不覆盖用户原文件，目录搬迁使用设计的精确映射。
- [x] 自审：C1–C6 名字与所有任务接口一致，schema 可空性/时间/金额/JSON字段来自当前模型，无隐含新表。
- [x] 自审：五条 Review Focus 均有明确测试归属，不用“适当处理边界情况”代替具体输入/断言。
- [x] 自审：每项含可检查红灯、最小实现、定向命令和审核点；读者不必自行决定事务所有者或模块归属。
- [x] 自审：结构阶段不混入 R/L、不顺带改 legacy 缺陷或格式；大范围目录调整有显式 D5 门槛。
- [x] 自审：最终报告要求分别标记结构/R/L 的实际状态，不以写完计划或单元绿灯宣称业务迁移完成。

文档校验证据：19 个任务/19 段 Python 测试示例语法检查；Task 1–13 的 76 项明确源文件声明、Task 1–15 的 85 次 pytest 目标引用检查；设计链接、审批停止点及行尾空白检查。代码块只是未来回归测试示例，本轮没有运行这些应用测试。

## 执行交接

请先审阅本计划及 C1–C6 接口。可选方式：逐任务子代理实现/独立复核，或单会话原生逐项执行后统一复核；本计划建议前者，因为事务与类型跨模块耦合，确认/交接的错误代价较高。无论选哪种方式，任务依赖串行、MySQL验收串行，先执行 Task 1–13；到 D5/R/L 门槛再次确认，不自动扩大范围。

实施已启动，逐任务结果见本计划的 checkbox 与独立验证报告；不得把执行方式/测试环境授权视为 D5/R/L 或 git 提交/合并授权。
