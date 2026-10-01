# Shopping Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有桌面展厅中交付可检索真实商品、动态调用工具、提出购物操作、跨轮恢复并接受任务评测的购物 Agent。

**Architecture:** 单购物 Agent 的 LangGraph 工具循环负责调查与选择下一步；MySQL 的 Task/Run/步骤日志负责执行与恢复。商品、报价、库存、购物车和结账继续由现有确定性服务提供，用户确认在模型循环外执行。检索采用 MySQL 全文索引与 Milvus 向量召回融合。

**Tech Stack:** Python、FastAPI、Pydantic、SQLAlchemy async + asyncmy、Alembic、LangGraph、现有 ChatOpenAI 适配方式、AsyncMilvusClient、React + TypeScript + Vite、pytest、Vitest、Playwright。

**Spec:** [已通过的设计](../specs/2026-10-01-shopping-agent-design.md)。本文覆盖阶段 A 的站内闭环；可选网页研究见[附属计划](./2026-10-01-shopping-agent-web-research.md)。B/C 阶段不在本次实施范围。

状态：待计划审阅及执行方式选择；以下测试均为实施时的要求，尚未执行。

## Global Constraints

- 用户通过一个聊天框表达需求；不增加类目、检索词或预算表单。沿用现有桌面展厅与可隐藏会话侧栏。
- 第一阶段采用单 Agent；不新增 Agent 框架，不实现真实付款、订单履约、退款、多 Agent 分工、开放式电脑控制或无人值守购买。
- 模型调用最多 8 次，工具执行最多 12 次；失败重试计入总数，每次调用至多重试 1 次，只读工具并发最多 4 个。
- 单次模型超时 15 秒；单 Run 入队起最多 120 秒，收尾预留 5 秒；新 Run 租约 30 秒，每 10 秒续期，最多两次执行尝试。
- 累计输入 token 预算 64k、输出 8k；调用前持久化预留，接管不重置预算。每用户轮次最多 1 个购物操作提案。
- 词法最多 50 + 向量最多 50 + 精确型号候选；RRF 初始 `k=60`；核验后最多 20 个候选，展示最多 3 个，比较 2–3 个。
- Session 默认 24 小时、操作默认 10 分钟；任务/消息默认保留 30 天。历史读取不恢复交易权限。
- 金额是整数最小货币单位；站内交易沿用 CNY。未知库存/规格不是符合条件，历史美元价不是当前价格，模拟价格与库存必须标明。
- `principal/session/task/run/lease` 由服务端注入；确认、实际结账及任意网络写入不注册为模型工具。
- 用原生异步 I/O；模型及网络调用不持有数据库锁。连接池、worker 并发、工具并发分别限制。
- 60 个固定任务，各至少重复 3 次；总成功运行比例至少 85%；检索 `Recall@20` 目标至少 90%，整体不得低于词法基线；关键权限与幂等用例全部通过。
- 正常交互目标：受理/首个进度 p95 ≤ 1 秒，普通站内推荐完成 p95 ≤ 30 秒；记录运行环境，不以 stub 耗时代表真实模型性能。
- 不覆盖现有下载/导入数据，不重置开发库或 Redis，不暂存嵌套参考仓库、密钥、原有无关修改。参考代码先核验再适配。

## Review Focus

1. “不要低于 500”“预算提高到 800”“两件合计 1000”和已有同款购物车行：方向、单位、数量及最终小计正确，歧义追问（Task 4、9）。
2. 一个用户的多个会话共享购物车，新旧 API 并发：会话锁不能代替购物车版本和跨入口门禁（Task 3、9）。
3. 提案/确认已提交但响应未保存、worker 失联、停止与确认竞争：重启后返回真实业务结果，不重复写入（Task 9、13）。
4. 英文长标题、短型号、失效向量、目录外伪造 ID、缺规格和用户说“第二个”：相关性、来源、显示顺序与交易资格分别核验（Task 4、7、11）。
5. 刷新丢失 run_id、切换会话后旧响应迟到、确认后网络中断、身份切换：恢复正确轮次且不串户、不重复提交（Task 14–16）。

## 执行准备、文件边界与共享契约

执行时使用隔离 worktree，沿用用户此前对隔离工作区的同意；开始前读取 `AGENTS.md`、设计与参考索引。当前主目录另有 `docs/superpowers/plans/2026-09-28-catalog-pricing-inventory.md`、`ecommerce-agent-references/.gitignore` 的未提交修改，原样保留。不要复制或链接会被测试清空的开发库配置。

Python 命令从仓库根运行，使用 `.venv/bin/python`；Node 满足现有 `frontend/package.json` 的 `>=22.12`。测试沿用 `asyncio.run`，不因示例改用项目尚未安装的 pytest async 插件。MySQL 测试必须使用 `tests/conftest.py` 验证的独立 `ECOM_TEST_DATABASE_URL`；该夹具会 downgrade，禁止对开发库使用。MySQL 测试串行，执行者不得让多个测试进程共享此库。

当前迁移头为 `0008_commerce_events`，计划新增 `0009_agent_runs → 0010_catalog_search → 0011_agent_action_context`。若执行时头已变化，先协调迁移顺序，不创建分叉。

| 文件组 | 职责与边界 |
| --- | --- |
| `models/shopping_agent.py` | 新 Agent 边界 DTO；单向引用现有 `TurnResponse`，不让旧 commerce schema 反向导入形成循环 |
| `shopping_session/agent_models.py`、`run_types.py`、`run_repository.py`、`run_coordinator.py` | 持久化结构、内部执行身份、步骤/消息读写、租约与原子完成 |
| `commerce_orchestrator/requirements.py`、`agent_context.py`、`agent_tools.py`、`agent_read_tools.py`、`agent_action_tools.py` | 需求验证、上下文、工具注册、读工具与提案工具 |
| `commerce_orchestrator/agent_model.py`、`agent_graph.py`、`agent_response.py` | 模型协议、动态图、响应核验；旧 `graph.py/model.py` 保留回退 |
| `catalog/search_models.py`、`search_documents.py`、`lexical_search.py`、`hybrid_search.py`、`search_types.py` | 可重建检索文档与召回；不把目录 CRUD 再复制一套 |
| `services/catalog_embeddings.py`、`catalog_indexer.py`、`shopping_agent_runtime.py`、`agent_worker.py`、`agent_evaluation.py` | embedding、索引同步、装配、worker、任务评测 |
| `api/agent_runs.py`、`scripts/shopping_agent_worker.py`、`scripts/evaluate_shopping_agent.py` | HTTP 与运维入口，执行逻辑留在服务中 |
| `frontend/src/features/chat/agentRunStore.ts`、`agentRunApi.ts`、`AgentProgress.tsx` | 新运行传输、状态与进度；复用原商品和确认 UI |

具体文件由所属 Task 创建；Modify 所列文件已存在，Create 所列为新增。每项最终仅暂存本任务文件并提交，不使用 `git add .`。以下函数均指对应类方法，除非标明纯函数。

### Task 1: Agent 契约、错误与调用预算

**Files:** Create `models/shopping_agent.py`、`shopping_session/run_types.py`、`tests/test_agent_contracts.py`；Modify `config/settings.py`、`.env.example`。

**Interfaces:**
- `AgentTurnRequest(turn_id, message, product_refs, task_id=None, respond_to_action_id=None, idempotency_key=None, retry_of_run_id=None)`；message 最大 2000 字符，refs 最多 3，每项 `ProductRef(product_id, sku_id=None)`，未知字段拒绝。
- `RequirementState(version, goal, constraints, budget, preferences, open_questions, rejected_product_ids, last_presented_product_ids)`；`RequirementPatch(changes, new_goal=False)` 中每项修改带 `source_turn_id/source_span/strength`；`Budget(amount_minor, currency, scope, quantity)` 的 scope=`unit|line_total`。
- `EvidenceRef` 按设计 §5.3；`ToolCall(id, name, arguments)`；`ToolResult(status, data, evidence_refs, error_code, retryable, truncated)`，status=`ok|empty|unavailable|invalid_arguments`。
- `AnswerDraft(kind, segments, product_ids, comparison_dimensions, question, action_id)`，每段 `AnswerSegment(text, evidence_ids)`；`ModelDecision(tool_calls, answer, input_tokens, output_tokens)` 中 tool_calls 与 answer 二选一。
- `AgentTurnResult(task_id, run_id, message, evidence, commerce: TurnResponse | None, error_code=None, incomplete=False)`；`RunView(run_id, task_id, status, result, error_code)`；`EventPage(events, next_seq)`、`MessagePage(messages, next_cursor)`。公开事件含 seq/phase，phase=`accepted|searching|checking|comparing|waiting|completed|failed|cancelled`，说明文字由服务端阶段映射生成。
- `AgentLimits` 固定上文初始值，token 数取输入 64000、输出 8000；`BudgetState` 保存已用/预留次数、token 和 reservation_id。内部 `RunClaim(turn: TurnClaim, run_id, task_id, attempt, deadline_at, checkpoint_version)`，`RunSnapshot(request, principal: Principal, requirements, steps, budget, claim)`；身份不出现在模型 DTO。

- [ ] 写 `test_limits_and_input_boundary`：
  ```python
  assert AgentLimits().model_calls == 8
  assert AgentLimits().tool_calls == 12
  assert AgentLimits().run_seconds == 120
  assert AgentTurnRequest.model_validate({'turn_id': 't', 'message': '耳机'}).product_refs == []
  # 另用 pytest.raises(ValidationError) 覆盖 principal_key、price、负预算、bool 数量、未知 kind。
  ```
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_contracts.py -q`，确认新契约缺失导致失败。
- [ ] 实现上述 DTO；配置前缀沿用 `ECOM_`，增加 `shopping_agent_enabled=False`、`shopping_agent_web_enabled=False`、`shopping_agent_worker_concurrency=2` 及 `shopping_agent_*` 预算项；服务端校验续期间隔小于租约，模型超时不超过总时限。
- [ ] 同一测试文件增加 `test_model_decision_is_call_or_answer`、`test_invalid_lease_settings_rejected`，断言双输出和无效预算配置不能启动；运行该文件及 `tests/test_commerce_model.py` 全通过。
- [ ] 提交 `feat: define shopping agent contracts and limits`。

### Task 2: Run、步骤与会话消息持久化

**Files:** Create `shopping_session/agent_models.py`、`shopping_session/run_repository.py`、`migrations/versions/0009_agent_runs.py`、`tests/test_agent_runs_mysql.py`；Modify `shopping_session/models.py`、`migrations/env.py`、`tests/test_shopping_session_mysql.py`。

**Interfaces:** `RunRepository(factory)`；`async snapshot(run_id: str) -> RunSnapshot`；`async append_step(db: AsyncSession, claim: RunClaim, *, kind: str, payload: dict, public_event: dict | None = None) -> int`；`async events(principal: Principal, session_id: str, run_id: str, after_seq: int) -> EventPage`；`async messages(principal: Principal, session_id: str, cursor: str | None, limit: int = 50) -> MessagePage`。调用方控制写事务及租约检查。

- [ ] 写 `test_run_schema_and_versioned_response`，核心断言为 `assert {'contract_version'} <= schema['columns']['commerce_turns']`、`assert old_turn.contract_version == 1`；同时验证四张新表、`(session_id,turn_id)` 唯一和 `active_run_id` 可空。`test_owned_history_read_after_expiry` 断言仅所属主体能读过期历史，仍不能执行。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_runs_mysql.py -q`，确认新表/接口缺失失败，不能以 skipped 视为通过。
- [ ] 创建设计 §9.2 的四张表和既有字段；Run 额外存服务端可信 `actor_issuer/actor_subject`，恢复时重算 principal_key 与 Session 对比，不保存 token。任务的状态约束、Run 的队列/期限索引、步骤顺序和消息去重索引全部落库。
- [ ] 实现版本化读取及步骤写入；工具参数、结果、工作状态和预算保留明确的记录类型；公开事件与内部日志分离。`active_run_id` 的循环引用使用可空字段和一致性检查，避免迁移建表顺序造成循环外键。
- [ ] 修正旧迁移测试：检查历史 revision 后、使用新增列的 ORM 前升级到 head；保留真实 downgrade/upgrade 断言。运行上述文件和 `tests/test_shopping_session_mysql.py`，验证从 `0008` 升级保留旧响应，降级只在隔离测试库进行。
- [ ] 提交 `feat: persist agent runs steps and messages`。

### Task 3: 入队、租约和新旧入口共享门禁

**Files:** Create `shopping_session/run_coordinator.py`、`tests/test_agent_coordinator_mysql.py`；Modify `shopping_session/service.py`、`shopping_session/schemas.py`、`cart/service.py`、`checkout/service.py`。

**Interfaces:** `RunCoordinator(factory, sessions, repository, limits, pending_actions=None)`；`async enqueue(principal, session_id: str, request: AgentTurnRequest) -> RunView`；`async claim_next() -> RunClaim | None`；`async renew(claim: RunClaim) -> RunClaim`；`async assert_current(db, claim) -> None`；`async finish(claim, result: AgentTurnResult, requirements: RequirementState, *, task_status: str, run_status: str, seen: list[ProductRef]) -> None`。`ShoppingSessionService.assert_write_access(db, principal, session_id, source_turn: TurnClaim | None) -> None` 给旧入口和领域写服务共用。pending_actions 是 Task 9 的领域撤销接口，前置单测注入同签名 fake。

- [ ] 写 `test_enqueue_replays_without_duplicate_message`：`assert first.run_id == replay.run_id`、`assert len(user_messages) == 1`；同 turn_id 内容或契约版本变化抛 `idempotency_conflict`。`test_parallel_enqueue_has_one_owner`：两个不同轮次只有一个受理，另一方 `run_in_progress`。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_coordinator_mysql.py -q` 确认失败。
- [ ] 实现会话行锁、请求哈希、版本化响应和排队占用；新轮次、消息、accepted 事件及 `active_run_id` 原子提交。旧版本哈希算法保持可回放，先校验 contract_version 再解析响应，不把新哈希算法强加到既有历史数据。无 task_id 时继续当前任务或创建首个任务；显式 task_id 只允许本会话，已完成任务在无其他活动目标时可由新输入重新激活。
- [ ] 实现领取、续期、fencing 和 `finish` 原子事务。统一锁顺序为 Session → Task/Run → Cart → PendingAction → 排序后的商品/价格/库存；队列先读取候选，再按此顺序锁定并复核，不能先锁 Run 再等 Session。
- [ ] 写 `test_old_and_new_entrypoints_share_gate`、`test_old_lease_cannot_append_or_finish`：新 Run 阻止旧 turn/直接确认，旧有效租约阻止新入队；令牌被替换后旧 worker 写入拒绝。无 source_turn 的直接业务写入也必须调用共享门禁。
- [ ] 运行该文件及 `tests/test_commerce_turns_mysql.py tests/test_cart_confirmation_mysql.py tests/test_checkout_mysql.py`，全部通过后提交 `feat: coordinate agent and legacy commerce execution`。

### Task 4: 需求更新、商品指代与上下文

**Files:** Create `commerce_orchestrator/requirements.py`、`commerce_orchestrator/agent_context.py`、`tests/test_agent_requirements.py`、`tests/test_agent_context.py`。

**Interfaces:** `validate_patch(current: RequirementState, patch: RequirementPatch, *, turn_id: str, message: str) -> RequirementState`；`to_shopping_intent(requirements: RequirementState) -> ShoppingIntent`；`validate_line_budget(requirements, unit_amount_minor: int, final_quantity: int) -> None`；`resolve_references(message: str, explicit: list[ProductRef], presented: list[ProductRef]) -> list[ProductRef]`；`build_context(snapshot: RunSnapshot, *, max_input_tokens: int) -> list[dict]`。以上为纯函数；目录类目解析在读工具执行。

- [ ] 写 `test_budget_direction_and_scope`，断言“不要低于 500”不能生成最高预算 500；对总预算100000、数量2，`assert to_shopping_intent(requirements).budget_max_minor == 50000`；`validate_line_budget(requirements, 50001, 2)` 抛业务验证错误。`test_inferred_budget_cannot_override_explicit` 断言推测不覆盖用户明确值。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_requirements.py tests/test_agent_context.py -q`，确认缺少验证器时失败。
- [ ] 实现来源片段精确包含、字段/运算符白名单、币种和单位归一化、版本递增；未支持的方向保留未决问题，不扭曲成现有 `ShoppingIntent` 可接受的相反条件。总额计算使用最终行数量，包括同 SKU 已有数量，范围不明先追问。
- [ ] 实现最近已提交商品集合中的序数引用；没有清晰参照或规格不明返回待澄清。旧摘要不是交易证据，截断优先保留本轮、约束来源、待确认动作和最近商品；确定性截断优先，若用模型压缩必须计入调用预算。
- [ ] 写 `test_second_item_uses_committed_order`、`test_context_truncation_preserves_constraints`、`test_web_text_cannot_update_requirements`，断言未提交工具结果不改变“第二个”、压缩不丢预算来源、网页片段不能伪装成本轮用户来源；运行两文件通过。
- [ ] 提交 `feat: validate conversational shopping requirements`。

### Task 5: 检索文档与 MySQL 词法召回

**Files:** Create `catalog/search_models.py`、`catalog/search_types.py`、`catalog/search_documents.py`、`catalog/lexical_search.py`、`migrations/versions/0010_catalog_search.py`、`tests/test_catalog_lexical_mysql.py`；Modify `migrations/env.py`。

**Interfaces:** `SearchDocument(product_id, text, content_hash, revision)`；`SearchHit(product_id, rank, source, score=None)`；`SearchQuery(text, variants, requirements, cursor=None)`；`SearchPage(candidates, next_cursor, incomplete, errors, evidence_refs)`。`build_search_document(product: ProductDetailView, revision: str) -> SearchDocument`；`LexicalSearch(factory).async search(query: SearchQuery, limit: int = 50) -> list[SearchHit]`，`async exact(query: SearchQuery, limit: int = 20) -> list[SearchHit]`、`async resolve_category(value: str) -> CategoryRefView | None`。

- [ ] 写 `test_document_hash_ignores_dynamic_price_and_stock`、`test_exact_model_bypasses_fulltext_short_word_limit`，断言 `assert before.content_hash == after.content_hash` 和 `assert requested_id in [hit.product_id for hit in hits]`；SQL 注入样式文本按数据处理。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_catalog_lexical_mysql.py -q` 确认失败。
- [ ] 新建检索文档表和 FULLTEXT 索引，展开标题、品牌、类目和白名单属性；参数化 MATCH 查询，精确型号/编码独立召回，不把整个 source_details 原样塞入提示词。类目 resolver 只确认目录中存在的 ID/编码/名称；多义或未命中保留查询词，不为填满类目字段而强制追问。
- [ ] 实现有界游标，绑定查询哈希、索引版本和偏移；非法或与查询不匹配的游标拒绝，不能拼接原始 SQL。无 FULLTEXT 环境报清晰依赖错误，不默默全表 LIKE 扫描冒充新检索。
- [ ] 增加 `test_search_documents_are_rebuildable_without_catalog_mutation`，断言重建文档不改变商品/报价/库存；运行该文件及 `tests/test_catalog_mysql.py` 通过。
- [ ] 提交 `feat: add catalog lexical search documents`。

### Task 6: 异步 embedding 与可恢复索引构建

**Files:** Create `services/catalog_embeddings.py`、`services/catalog_indexer.py`、`data/index_catalog.py`、`data/INDEX.md`、`tests/test_catalog_indexer.py`；Modify `infrastructure/milvus_client.py`、`config/settings.py`、`.env.example`、`.gitignore`。

**Interfaces:** `EmbeddingProvider.async embed(texts: list[str]) -> list[list[float]]`；`MilvusClient.async ensure_index(revision: str, dimension: int) -> None`、`async upsert_documents(revision: str, documents: list[SearchDocument], vectors: list[list[float]]) -> None`、`async delete_documents(revision: str, product_ids: list[str]) -> None`、`async search_documents(revision: str, vector: list[float], limit: int = 50) -> list[SearchHit]`；`CatalogIndexer.async sync(*, revision: str, resume: bool, reconcile: bool, limit: int | None = None) -> IndexReport`，report 含扫描/更新/跳过/失败/删除数量和模型元数据。

- [ ] 写 `test_resume_skips_successful_hashes`，第二次执行断言 `assert report.updated == 0`；`test_wrong_dimensions_fail_before_upsert` 断言 `milvus.upsert_documents.assert_not_awaited()`；`test_archive_removes_index_entry` 验证下架对账删除索引但不删除商品。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_catalog_indexer.py -q` 确认失败。
- [ ] 基于原生异步接口实现配置指定的 OpenAI 兼容 embedding 适配器；配置 `catalog_embedding_base_url/api_key/model/dimension`、`catalog_search_revision`、`catalog_search_collection_prefix=shopping_catalog`，不推断对话 Key 可用于 embedding。MockTransport 验证请求和错误，拒绝非有限向量或维度错误；启动前核对已安装异步 Milvus 方法与服务版本，不在单测访问外部模型。
- [ ] 实现小批量同步、进度、内容哈希、失败清单与按 revision 分开的恢复清单；索引清单含模型/维度/文本规则版本，不匹配时拒绝 resume。先 Milvus 写成功再标记成功，崩溃后允许幂等 upsert；首次成功建立新索引也不自动切换正在使用的版本。
- [ ] 新脚本命令契约：`.venv/bin/python data/index_catalog.py --revision demo_v1 --resume --reconcile`；`--limit 100` 用于受控冒烟。版本只接受小写字母/数字/下划线，以新前缀构造集合；文档写明配置、工作目录、断点和切换/回退步骤，忽略实际断点与报告内容；旧集合不删除。
- [ ] 运行该测试和 `tests/test_infrastructure_clients.py`，全部通过后提交 `feat: build resumable catalog vector indexes`。

### Task 7: 混合召回与按 ID 直接核验

**Files:** Create `catalog/hybrid_search.py`、`tests/test_catalog_hybrid_search.py`、`tests/test_agent_direct_facts.py`；Modify `catalog/search_types.py`、`commerce_orchestrator/facts.py`、`commerce_orchestrator/compare.py`、`commerce_orchestrator/discovery.py`。

**Interfaces:** `VerifiedCandidate(product: ProductDetailView, quote: SKUQuote, evidence_refs: list[EvidenceRef])`；`CatalogSearch(lexical, embeddings, milvus, catalog, facts).async search(query: SearchQuery) -> SearchPage`，`async resolve_category(value: str) -> CategoryRefView | None` 委托词法服务；`SearchPage.candidates: list[VerifiedCandidate]`；`CommerceFactsReader.async verify_products(product_ids: list[str], requirements: RequirementState) -> list[VerifiedCandidate]`；`rrf(rankings: list[list[SearchHit]], k: int = 60) -> list[SearchHit]` 是纯函数。

- [ ] 写 `test_rrf_uses_ranks_not_raw_scores`，同一商品在两个来源均第1时 `assert merged[0].score == pytest.approx(2 / 61)`；`test_hard_filter_before_top_twenty` 断言 `assert len(page.candidates) <= 20`，且先查真实事实和过滤再截断；召回结果不能凭向量里的价格通过预算。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_catalog_hybrid_search.py tests/test_agent_direct_facts.py -q` 确认失败。
- [ ] 实现词法/向量并发、精确型号加成及去重，允许一次有界扩展；从目录与 quote_many 批量核验。查询变体保留型号和排除项，模型提出的扩展仍受已验证约束限制。
- [ ] 实现指定 ID 的批量核验，替换 compare/selection 对最多 200 条扫描的资格依赖；用户面对的旧 compare 保留 seen/归属校验，Agent 内部比较可读本轮证据，但不自动记录 seen。
- [ ] 写 `test_selected_product_outside_first_two_hundred_is_valid`、`test_stale_vector_cannot_recommend_archived_product`、`test_semantic_outage_is_not_empty_catalog`、`test_missing_hard_attribute_is_unknown`；断言单一降级错误、真实 ID 有效、已下架排除、未知字段不能判为满足。
- [ ] 运行上述两文件及 `tests/test_commerce_discovery.py tests/test_commerce_facts.py`；通过后提交 `feat: retrieve and verify shopping candidates dynamically`。

### Task 8: 工具注册、上下文注入与只读工具

**Files:** Create `commerce_orchestrator/agent_tools.py`、`commerce_orchestrator/agent_read_tools.py`、`tests/test_agent_tools.py`；Modify `models/shopping_agent.py`、`shopping_session/run_coordinator.py`。

**Interfaces:** 内部 `ToolContext(snapshot: RunSnapshot)`、`ToolDefinition(name, arguments_model, handler, effect: read|requirements|proposal)`；`ToolRegistry.register(definition) -> None`、`schemas() -> list[dict]`、`async execute(call: ToolCall, context: ToolContext) -> ToolResult`。`register_read_tools(registry, search: CatalogSearch, catalog, facts, cart) -> None` 注册需求更新及 5 个站内读取工具。

- [ ] 写 `test_tool_identity_is_injected`，传入 principal/session_id/price 时 `assert result.status == 'invalid_arguments'`；`test_unknown_and_write_tools_rejected` 断言 `assert 'confirm_action' not in tool_names`，任意 SQL/HTTP 工具同样不注册；原始产品描述不能修改 registry。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_tools.py -q` 确认失败。
- [ ] 注册 `update_requirements/search_catalog/get_product_details/quote_skus/compare_products/get_cart`；每个参数用独立 Pydantic 模型拒绝额外字段。需求工具通过 `search.resolve_category` 核验类目提案再交 Task 4 验证；详情/报价批量上限 20、比较 2–3，身份从 snapshot 注入，非法 ID 和 SKU 关系返回明确错误。
- [ ] 实现合法引用集合 = 显式引用经校验的商品 + 本轮证据 + 已提交展示集合；工具只返回白名单字段及 EvidenceRef，区分 empty/unavailable/invalid_arguments。查询和比较都不直接更新 seen。
- [ ] 增加协调器 `async record_observation(claim, call: ToolCall, result: ToolResult, *, requirements: RequirementState | None = None, new_goal: bool = False) -> RunClaim`；仅需求工具的已验证结果可更新 working_state，与步骤日志同事务。明确 new_goal 时结束旧 Task、调用领域接口失效旧提案、创建新 Task 并改绑当前 Run，返回新 claim；模糊目标变化追问，不自行清空约束。同目标改约束也使旧提案失效。
- [ ] 写 `test_requirement_result_and_state_are_atomic` 和 `test_new_goal_rebinds_run_without_reviving_old_action`，注入保存失败断言状态/日志都未提交；目标切换后 Run 只属于新 Task，旧卡不可执行。正常运行时存在待确认动作却未装配领域撤销器必须失败，不能跳过撤销。
- [ ] 运行本文件及 Task 4、7 的测试，通过后提交 `feat: expose bounded shopping read tools`。

### Task 9: 交易提案、确认和业务结果恢复

**Files:** Create `commerce_orchestrator/agent_action_tools.py`、`migrations/versions/0011_agent_action_context.py`、`tests/test_agent_actions_mysql.py`；Modify `cart/models.py`、`cart/schemas.py`、`cart/service.py`、`checkout/service.py`、`commerce_orchestrator/service.py`、`tests/test_cart_mysql.py`。

**Interfaces:** `AgentActionService(sessions, cart, checkout)`；`async propose(context: ToolContext, kind: str, target: dict) -> ToolResult`，成功时 `data` 为含 action_id 的领域提案 JSON；`async confirm(snapshot: RunSnapshot) -> AgentTurnResult | None`；`async recover(snapshot: RunSnapshot) -> AgentTurnResult | None`；`async invalidate_pending(db, principal, session_id: str, *, source_turn_id: str | None = None, task_id: str | None = None) -> None`。在 `cart/schemas.py` 定义仅内部使用的 `ActionContext(task_id, requirement_version, requirements)`，加入领域提案接口的可选参数，旧调用默认 None。

- [ ] 写 `test_proposal_never_mutates_cart`，断言 `assert after_proposal.version == before.version` 及各行数量相等；`test_same_turn_proposal_replay_or_conflict` 断言 `assert first.data['action_id'] == replay.data['action_id']`，不同参数冲突；`test_confirm_twice_changes_quantity_once` 断言第二次确认后的数量等于第一次。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_actions_mysql.py -q` 确认失败。
- [ ] PendingAction 增加可空 task_id、约束版本和约束快照；领域服务使用已验证的本轮约束而不是过期 `claim.state`。对最终行小计检查总预算，价格变化后生成替代提案时保留完整动作上下文，不能丢约束或用户选择依据。
- [ ] 实现提案两工具与唯一明确确认绑定；裸“确认”仅能由服务端绑定唯一有效动作；含动作 ID 的确认/拒绝绕过 LLM。将原 `CommerceOrchestrator.chat_confirmation` 的业务规则复用到公共路径，保持原短语和 approved 布尔校验，不另造允许模糊批准的模型分支。
- [ ] 写 `test_changed_requirements_invalidate_old_action`、`test_cross_session_cart_version_conflict`、`test_confirmation_committed_before_response_is_recoverable`、`test_cancel_confirmation_lock_race`：参数/预算变更使旧卡失效；另一会话改车触发版本冲突；重启返回已成功结果；停止不能把已执行动作报告为未执行。
- [ ] 运行本文件及 `tests/test_cart_confirmation_mysql.py tests/test_checkout_mysql.py tests/test_commerce_chat_confirmation.py`，必要的旧迁移测试在旧 revision 检查后升到 head；通过后提交 `feat: preserve action approval across agent recovery`。

### Task 10: 可验证的模型工具调用适配器

**Files:** Create `commerce_orchestrator/agent_model.py`、`scripts/check_shopping_agent_model.py`、`tests/test_agent_model.py`；Modify `config/settings.py`、`.env.example`。

**Interfaces:** `AgentModel.async decide(messages: list[dict], tools: list[dict], *, timeout: float, max_output_tokens: int) -> ModelDecision`；`estimate_input_tokens(messages, tools) -> int`；`async probe() -> ModelCapabilities`，capabilities 含 model_id、tool_calling、structured_answer、checked_at 和 error_code。现有 `ECOM_LLM_*` 复用，新增输出上限不能突破剩余 8k 预算。

- [ ] 写 `test_tool_call_ids_and_results_round_trip`，断言 `assert [c.id for c in decision.tool_calls] == ['call-1', 'call-2']`，两个结果保留对应 ID；`test_invalid_json_is_explicit_error`、`test_plain_text_is_not_fake_tool_support` 断言坏 JSON 是明确错误、普通文本不会令 `capabilities.tool_calling` 变成 True。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_model.py -q` 确认失败。
- [ ] 在现有异步 ChatOpenAI 基础上适配结构化工具与答案；支持提供方原生结构化输出时优先使用，否则以明确 JSON schema 验证最终答案。禁用 SDK 隐式多次重试，所有重试由 Run 预算控制；不把提供方私有 reasoning 内容保存为对话步骤。
- [ ] 编写显式冒烟命令 `.venv/bin/python scripts/check_shopping_agent_model.py`：使用无业务写入的本地 echo 工具，经过真实模型调用→工具结果→最终答案，输出能力元数据，不输出 Key；默认单测只使用 fake client，不自动产生外部费用。
- [ ] 写 `test_timeout_and_usage_are_reported`，断言 timeout 可见且 usage/估算来源有记录；运行本文件及 `tests/test_commerce_model.py` 通过，提交 `feat: add verified agent model tool calling`。

### Task 11: 有来源的回答组装与旧业务卡片复用

**Files:** Create `commerce_orchestrator/agent_response.py`、`tests/test_agent_response.py`；Modify `shopping_session/run_types.py`。

**Interfaces:** `AgentOutcome(result: AgentTurnResult, requirements: RequirementState, task_status: str, seen: list[ProductRef])`；`ResponseAssembler(catalog, facts).async assemble(snapshot: RunSnapshot, draft: AnswerDraft, evidence: list[EvidenceRef]) -> AgentOutcome`。无副作用，实际消息、seen 与任务保存交给 `RunCoordinator.finish`。

- [ ] 写 `test_unknown_or_cross_product_evidence_rejected`，伪造 ID/错配引用使用 `pytest.raises(CommerceError, match='invalid_evidence')`；`test_card_money_comes_from_fresh_quote` 断言展示金额等于最新服务端整数值；无法验证的推荐不进入可购买卡片。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_response.py -q` 确认失败。
- [ ] 实现 answer/shortlist/compare/clarify/propose_action/unavailable 的结构化输出；纯知识回复 `commerce=None`，其他重用旧 DTO。最多 3 个商品，组装最终展示顺序，明确模拟事实、未知属性和不可用来源，交易成功结果直接使用领域结果。Task 状态映射：answer/compare→completed，shortlist/clarify/unavailable→waiting_input，propose_action→waiting_confirmation；确认成功→completed，用户拒绝→waiting_input。
- [ ] 写 `test_tool_candidates_not_committed_to_seen`、`test_no_unreferenced_price_in_explanation`、`test_explain_unknown_without_invented_attributes`；对金额和库存等可机械识别的事实使用引用值构造展示文本；语义建议允许模型表达，但不宣称来源验证能证明所有自然语言正确性。
- [ ] 运行本文件和 `tests/test_storefront.py tests/test_commerce_graph.py` 通过；提交 `feat: assemble grounded shopping agent responses`。

### Task 12: 有界动态工具循环

**Files:** Create `commerce_orchestrator/agent_graph.py`、`tests/test_shopping_agent_graph.py`；Modify `shopping_session/run_coordinator.py`、`commerce_orchestrator/agent_tools.py`。

**Interfaces:** `AgentDependencies(model, registry, repository, coordinator, responses, actions, limits)`；`ShoppingAgent(deps).async run(snapshot: RunSnapshot) -> AgentOutcome`。`RunCoordinator.async reserve_call(claim, *, kind: str, input_tokens: int = 0, output_tokens: int = 0, tool_name: str | None = None) -> BudgetState`、`async reconcile_usage(claim, reservation_id: str, input_tokens: int, output_tokens: int) -> None`；BudgetState 返回本次 reservation_id。

- [ ] 用脚本化模型写 `test_search_detail_search_again_then_answer`，断言 `assert tool_names == ['search_catalog', 'get_product_details', 'search_catalog']`；`test_direct_compare_does_not_force_discovery` 断言无需固定经过检索；`test_confirmation_never_calls_model` 断言 `assert model.call_count == 0`。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_shopping_agent_graph.py -q` 确认失败。
- [ ] 构建 `context → model → tools → model` 条件循环，完成/追问交给 assembler；提案一旦创建立即结束。依赖动作串行，只读 semaphore=4；同批包含需求更新和读取时按模型给出的顺序逐一执行，避免读取旧预算。
- [ ] 模型请求前持久化其输入摘要、预算预留，收到后先保存完整 tool_call 列表再发工具。工具调用同样预留预算并保存参数；工具结果按 ID 配对，接受 `record_observation` 返回的新 claim，并重新读取 snapshot 后继续，不能使用旧预算或旧 Task。接管时补齐未完成调用或标记明确错误，不向模型发送孤立的 tool_result。
- [ ] 写 `test_eighth_model_call_is_last`、`test_thirteenth_tool_never_executes`、`test_two_identical_observations_stop_loop`、`test_resume_does_not_reset_reserved_tokens`、`test_proposal_ends_tool_loop`，断言精确调用数、累计预算及终止行为；格式修复和 transient retry 同样消耗次数。
- [ ] 写 `test_budget_exhaustion_returns_verified_partial_results`，断言 `assert outcome.result.incomplete is True` 且商品 ID 都来自已核验证据；没有可用事实时返回 unavailable。实现无需再调用模型的保守收尾，不为凑满卡片添加虚构商品。
- [ ] 运行本文件及 Task 8、10、11 测试通过；提交 `feat: run bounded dynamic shopping tool loops`。

### Task 13: Worker、取消、过期清理和故障恢复

**Files:** Create `services/agent_worker.py`、`services/agent_metrics.py`、`scripts/shopping_agent_worker.py`、`tests/test_agent_worker_mysql.py`、`tests/test_agent_metrics.py`；Modify `shopping_session/run_coordinator.py`、`shopping_session/run_repository.py`。

**Interfaces:** `AgentWorker(coordinator, repository, agent, actions, limits)`；`async run_once() -> bool`、`async run_forever(stop: asyncio.Event) -> None`。协调器新增 `async cancel(principal, session_id: str, run_id: str) -> RunView`、`async recover_expired(now: datetime) -> int`；repository 新增 `async purge_history(before: datetime, batch_size: int = 100) -> int`。worker CLI 在 Task 14 装配完成后可直接启动。

- [ ] 写 `test_worker_reclaims_after_thirty_seconds`，断言 `assert second.turn.lease_token != first.turn.lease_token`、`assert second.deadline_at == first.deadline_at`；`test_old_worker_result_cannot_commit` 验证旧 worker 无权提交、最多两次尝试。用注入时钟和数据库租约代替真实等待。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_worker_mysql.py -q` 确认失败。
- [ ] 实现独立 worker、10 秒心跳、可停止轮询和限并发；数据库短事务与外部 I/O 分离。失去续期后停止调度，旧调用迟到不入库；退出关闭客户端与连接池，不只取消 asyncio Task 留连接泄漏。
- [ ] 实现 `AgentMetrics.record(event: dict) -> None`，白名单包含关联 ID、阶段、延迟、候选/过滤数、重试、usage 和版本；公开阶段事件在实际工具启动或状态提交时产生。`test_metrics_redact_secrets_and_history` 断言不输出 Key/令牌/完整历史，`test_latency_and_usage_are_correlated` 断言同 run_id 能合并步骤成本；运行 `.venv/bin/python -m pytest tests/test_agent_metrics.py -q` 验证。
- [ ] 实现恢复、取消及 120 秒超时终结。先查确认/提案的真实结果：成功动作恢复结果，未发布提案撤销，无成功事实才终态失败；终态结果保存为 contract_version=2，整个 finalization 保持 Task/Run/Turn/消息/seen 一致。
- [ ] 写 `test_crash_after_proposal_before_journal_recovers_same_action`、`test_crash_after_confirmation_returns_success`、`test_expired_session_releases_run_without_restoring_permission`、`test_finalize_rollback_leaves_no_half_message`；覆盖数据库提交的两侧故障点。
- [ ] 实现 30 天清理，仅处理已过期且无活动 Run 的会话；删除从属消息/步骤/Run，清空关联 v2 Turn 响应及 Session 的 Agent 约束内容，再处理过期 Task。被动作审计引用的 Task 仅保留关联元数据，业务动作和去重元数据不删除。`test_retention_removes_transcript_copies` 验证所有这些聊天副本均清除；运行本文件及 Task 2、3、9 数据库测试通过，提交 `feat: recover and cancel durable agent runs`。

### Task 14: HTTP 运行接口、能力装配与启停

**Files:** Create `api/agent_runs.py`、`services/shopping_agent_runtime.py`、`tests/test_agent_runs_api.py`；Modify `api/runtime.py`、`api/commerce.py`、`main.py`、`shopping_session/schemas.py`、`scripts/shopping_agent_worker.py`。

**Interfaces:** `build_agent_runtime(settings, factory, *, catalog, facts, sessions, cart, checkout) -> AgentRuntime`，runtime 提供 coordinator/repository/worker/capabilities 与 `async aclose()`；GET `/api/v1/commerce/capabilities` 返回 `agent_enabled/tool_calling/semantic_search/web_research` 的状态及原因，不返回密钥。

- [ ] 写 `test_run_routes_require_owner`、`test_accepted_run_can_be_polled_and_replayed`：`assert accepted.status_code == 202`、`assert duplicate.json()['run_id'] == accepted.json()['run_id']`；其他主体的 Run/messages/events/cancel 返回404或认证失败，不泄露状态。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_runs_api.py -q` 确认接口缺失导致失败。
- [ ] 实现设计 §11 的 POST runs、GET run/events/messages、POST cancel，参数严格校验；events 游标默认 0、页上限 100，messages 默认 50、上限 100，游标与 session/run 绑定。响应不缓存，终态不再持续生成进度。
- [ ] 现有 GET session 加 `active_run_id`；读取过期历史可用专用只读所有权检查，执行仍用 active 检查。定义新/旧契约版本冲突返回409；第二独立轮次返回409 run_in_progress，并仅向所属主体返回当前 run_id。
- [ ] 装配现有服务和新增 Agent 服务，旧 `/turns` 保留；默认开关关闭。`shopping_agent_enabled` 控制新受理和前端能力，关闭后 worker 仍可处理已经受理的运行。能力检查为显式 probe 的结果或未知，不在每次 GET 发收费模型请求。独立 worker 入口调用同一装配函数，所有服务使用同一配置与协调器。
- [ ] 写 `test_bootstrap_has_no_agent_background_execution`、`test_feature_off_keeps_legacy_route`、`test_refresh_can_find_active_run`；断言 FastAPI 不用 BackgroundTasks 执行 Agent、旧行为可回退、丢失浏览器 run_id 仍能恢复。
- [ ] 运行本文件及 `tests/test_commerce_auth_api.py tests/test_cart_confirmation_api.py tests/test_checkout_api.py tests/test_api_routes.py` 通过；提交 `feat: expose durable shopping agent run APIs`。

### Task 15: 前端运行状态、轮询与恢复

**Files:** Create `frontend/src/features/chat/agentRunApi.ts`、`frontend/src/features/chat/agentRunStore.ts`、`frontend/src/features/chat/agentRunStore.test.ts`；Modify `frontend/src/api/contracts.ts`、`frontend/src/api/client.ts`、`frontend/src/features/chat/sessionStore.ts`、`frontend/src/features/chat/historyStore.ts`、`frontend/src/features/chat/useShoppingSession.ts`、`frontend/src/features/chat/sessionStore.test.ts`。

**Interfaces:** TypeScript DTO 对齐 Task 1；`AgentRunApi.start(sessionId, body): Promise<RunView>`、`get(sessionId, runId)`、`events(sessionId, runId, afterSeq)`、`cancel(sessionId, runId)`、`messages(sessionId, cursor?)`；`AgentRunStore.start(body)`、`resume(sessionId, runId)`、`stop()`、`dispose()`，从现有 `ApiClient` 注入，不新增第二套令牌管理。

- [ ] 写 `send preserves raw text and references`，断言 `expect(sent.message).toBe('第二个加两件')`、`expect(sent).not.toHaveProperty('quantity')`，不附猜测的 product_ids；`network retry reuses frozen body` 断言重发 JSON 与 turn_id/action 幂等键完全一致。
- [ ] 运行 `npm --prefix frontend test -- --run src/features/chat/agentRunStore.test.ts`，确认新状态层不存在导致失败。
- [ ] 新模式使用原文与显式 ProductRef，删除该路径里的 ordinal/数量/比较正则；旧模式保留兼容实现。读取 capabilities 后选择模式，Agent 不可用时展示真实状态，不在已受理 Run 中途换旧 `/turns` 再执行。
- [ ] 实现约 1 秒轮询、失焦退避至 5 秒、终态停止、epoch 和 AbortController；Run 状态及公开事件更新 UI，最终 `commerce` 复用当前卡片/购物车处理。`dispose` 只取消客户端轮询，不隐式请求服务端 stop。
- [ ] 写 `refresh recovers run from session`、`late response cannot replace new session`、`expired history stays readable`、`identity switch clears active run`、`terminal retry creates new turn`，用 fake timers 验证边界；网络重试与失败后的用户主动重试分开处理。
- [ ] 当前本地历史保留为缓存/旧轮次回退，新消息以服务端已提交历史为准；按 turn_id 合并本地未受理消息。重试记录增加 contract_version，旧数据默认1；已受理 v2 Run 即使新受理开关关闭仍沿 v2 查询，不换旧 `/turns` 重做。`feature rollback still resumes accepted v2 run` 断言此边界。运行新测试与 `sessionStore.test.ts`、`review-regressions.test.tsx`、`api/client.test.ts` 通过；提交 `feat: recover shopping agent runs in the storefront`。

### Task 16: 聊天进度、来源与停止交互

**Files:** Create `frontend/src/features/chat/AgentProgress.tsx`、`frontend/src/features/chat/AgentProgress.test.tsx`、`frontend/e2e/agent.spec.ts`、`frontend/e2e/agentFixtures.ts`；Modify `frontend/src/features/chat/ChatPanel.tsx`、`frontend/src/features/chat/ChatPanel.module.css`、`frontend/src/features/chat/ChatPanel.test.tsx`、`frontend/src/features/chat/ActionSummary.tsx`、`frontend/src/features/chat/HistoryPanel.tsx`、`frontend/e2e/fixtures.ts`。

**Interfaces:** `AgentProgress({phase, stopped, onStop})`；阶段来自有序公开事件 `accepted/searching/checking/comparing/waiting/completed/failed/cancelled`，显示中文说明。聊天文本和 evidence 来源链接与现有商品卡、比较卡、动作卡共同展示。

- [ ] 写组件测试，断言 `expect(screen.getAllByRole('textbox')).toHaveLength(1)`；运行中可编辑草稿、发送禁用而停止可用，追问后输入恢复；来源展示可访问标签，web 引用不变成站内加购按钮。
- [ ] 运行 `npm --prefix frontend test -- --run src/features/chat/AgentProgress.test.tsx src/features/chat/ChatPanel.test.tsx` 确认新行为失败。
- [ ] 复用现有 motion、色彩和布局；不显示工具 JSON、私有推理或伪造进度百分比。停用历史失效动作卡，任务切换后的旧动作保持可读而不可提交；停止请求进行中也避免连续发送多个 cancel。
- [ ] 加入 reduced-motion、焦点、停止和阶段快速切换测试，确保动画可中断；响应已成功时取消返回真实业务结果，UI 不将其替换成“购物车未修改”。
- [ ] Playwright 覆盖原始中文需求→检索卡→第二个商品指代→提案→明确确认；刷新进行中 Run；另一会话的迟到响应。`agentFixtures.ts` 通过页面路由夹具保存模拟服务端状态，跨页面刷新并注入网络中断；这验证 UI 恢复协议，真实 worker 崩溃由 Task 13 的 MySQL 测试和 Task 18 验收覆盖，不让每个 UI 测试收费调用模型。
- [ ] 运行 `npm --prefix frontend run typecheck`、`npm --prefix frontend test -- --run`、`npm --prefix frontend run build` 和指定测试服务上的 `npm --prefix frontend run test:e2e -- agent.spec.ts`；通过后提交 `feat: present shopping agent progress and evidence`。

### Task 17: 任务评测集、检索基线与报告

**Files:** Create `services/agent_evaluation.py`、`scripts/evaluate_shopping_agent.py`、`tests/fixtures/shopping_agent/tasks.json`、`tests/fixtures/shopping_agent/catalog.json`、`tests/fixtures/shopping_agent/queries.json`、`tests/fixtures/shopping_agent/policies.md`、`tests/test_agent_evaluation.py`、`docs/shopping-agent-evaluation.md`；Modify `.gitignore`。

**Interfaces:** `EvaluationTask(id, group, user_goal, initial_state, user_script, allowed_actions, assertions)`；`EvaluationResult(task_id, attempt, success, violations, latency_ms, usage, cost_estimate)`；`async evaluate_task(task: EvaluationTask, driver: EvaluationDriver) -> EvaluationResult`；driver 的 `async reset(initial_state)`、`async turn(message, refs)`、`async snapshot()` 在本任务实现 fake 和真实 API/隔离库两种适配。真实模式禁止使用开发业务库。

- [ ] 写 `test_success_requires_final_business_state`：回答“已加入”但车未变化时 `assert result.success is False`；`test_equivalent_valid_tool_paths_pass` 不限制唯一轨迹；`test_unauthorized_write_fails_regardless_of_language_score` 任一越权写入都失败。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_evaluation.py -q` 确认缺少评价器导致失败。
- [ ] 建立 60 条非重复目标：需求/中英检索15、约束与未知15、多轮10、交易10、权限故障10；固定商品与库存种子、虚拟时间、用户脚本。商品 fixture 使用测试自有 ID，不引用开发库全部导入数据或真实用户对话。
- [ ] 写确定性业务断言及禁止动作检查，语义解释单列人工/评审评分；查询集标注相关商品，含英语短型号和中文使用场景。计算每组成功率、总体成功次数/全部运行次数、Recall@20、词法对照、p50/p95 和 token/费用；未配置价格表时费用标记未知，不写 0。
- [ ] CLI：`.venv/bin/python scripts/evaluate_shopping_agent.py --mode fixture --suite core --repeat 1`；真实执行使用 `--mode live --suite core --repeat 3`，命令启动时强制校验评测库与开发库不同。报告记录 prompt/tool/index/model 版本和失败轨迹，真实输出默认忽略，不进源码。
- [ ] 校验数据数量、每组最低数和查询标注覆盖；fixture 模式可进入 CI，live 模式单独运行。运行本文件和 Task 12 的图测试通过，提交 `test: evaluate shopping tasks against business outcomes`。

### Task 18: 部署、真实冒烟与阶段 A 验收

**Files:** Create `docs/shopping-agent.md`、`docs/superpowers/verification/2026-10-01-shopping-agent.md`、`tests/test_agent_runtime.py`；Modify `docker-compose.yml`、`README.md`、`.env.example`、`services/shopping_agent_runtime.py`、`scripts/shopping_agent_worker.py`。

**Interfaces:** `.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000` 运行 API；`.venv/bin/python scripts/shopping_agent_worker.py` 运行 worker 与清理循环；两者从同一 settings 读取连接和能力配置。原本只启动 API 的场景不会被悄悄当作 worker 正常。

- [ ] 写 `test_runtime_closes_owned_clients_once`，重复 aclose 后 `milvus.close.assert_awaited_once()`；补充 `test_agent_disabled_preserves_api_boot`、`test_no_embedding_config_reports_lexical_degradation`，断言旧 API 正常且缺 embedding 明确降级。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent_runtime.py -q` 确认所要求的新生命周期行为失败。
- [ ] 修正当前 `docker-compose.yml` API 仍使用 SQLite URL 的配置落差，改为外部提供的 `mysql+asyncmy` URL，并明确容器内使用 MySQL 服务名；移除模型 Key 的假值默认项。增加独立 agent-worker 服务，复用后端镜像与配置，不改数据库卷或用户现有连接值。关闭时不受理新任务，已有任务可完成或按租约恢复；文档记录 API/worker/索引三种进程的启动、停止、日志与排查方式。
- [ ] 执行真实模型能力 probe；在固定查询集上比较可用 embedding 配置，记录准确模型 ID/维度/版本和质量，选定后构建新索引并核对条数、失败清单及抽样召回。先 `--limit 100`，确认连接和成本后再完成目录索引；不重新生成价格/库存。
- [ ] 运行 `.venv/bin/python -m pytest tests/ -q`，必须单列真实 MySQL/Redis 用例的执行与 skipped 原因；`npm --prefix frontend run typecheck`、`npm --prefix frontend test -- --run`、`npm --prefix frontend run build`、指定隔离预览上的 `npm --prefix frontend run test:e2e`。已有基线失败应定位并说明，不能用排除测试假装全绿。
- [ ] 在隔离评测服务执行 60×3 live 任务，按设计门槛报告结果；检查真实请求轨迹证明至少完成一次“模型选择工具→真实目录/报价→模型答复”和一次明确确认后的单次写车。采样人工核查推荐理由，严重越权或虚构交易事实直接阻止发布。
- [ ] 人工验收桌面聊天、商品展示、动效、停止、追问、刷新恢复和确认；无法连接浏览器时保留明确的人工待验项，不把自动 E2E 当作人工验收已完成。
- [ ] 验证升级与回退：先共享门禁/兼容迁移，再 worker，再前端开关；回退先关闭受理并完成或取消旧 Run，再切旧 UI。报告中区分代码、离线测试、真实模型、索引、人工验收，不填未经执行的通过结论。
- [ ] 运行文档链接检查和 `git diff --check`；完成分支整体审阅后提交 `docs: document and verify shopping agent operations`。只有必要检查完成且指标满足才能标记阶段 A 完成。

## 依赖顺序与验收映射

顺序执行为默认。Task 5–7 的检索工作与 Task 2–4 的持久化/需求逻辑可在契约固定后分开推进，但共享测试库不能并发清理；任何并行执行仍需用户选择相应执行方式。

| 设计要求 | 对应任务 |
| --- | --- |
| §1–4 单 Agent、已有系统衔接、动态图 | 1、10、12、14、18 |
| §5 来源、约束、跨轮指代和模拟事实 | 4、7、8、11、15 |
| §6 工具权限和结果契约 | 1、8、9、12 |
| §7 混合检索、索引生命周期和缺失属性 | 5–7、17、18 |
| §8 可选网页研究 | 附属计划 W1–W3；主计划只提供注册和能力接口 |
| §9 持久化、暂停、恢复、取消、保留期 | 2、3、9、12–15 |
| §10 确认、主体归属、跨会话购物车 | 3、9、13、14 |
| §11 API、前端、旧流程兼容 | 14–16、18 |
| §12 预算、故障、并发、可观测性 | 1、8、10、12–14、18 |
| §13 任务/检索/故障评测及门槛 | 各 Task 的测试、17、18 |
| §14–16 交付、参考边界与分期 | 全计划；B/C 保留独立设计 |

完成某 Task 仅表示该任务的测试和审阅通过；不据此宣称真实模型、全量索引或完整 Agent 已验收。实施结束前保存完整检查结果，再按选定执行方式完成最终审阅和分支整合。
