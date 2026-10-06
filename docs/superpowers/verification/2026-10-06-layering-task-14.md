# Task 14 验收报告：顶层分组（grouped 布局）

日期：2026-10-06。范围：设计 §3 的 `grouped` 布局（**不含** `ecom/` 包根，那是 Task 15）。
基线：提交 `8779cf5`（Task 1–13 的 `original` 布局）。改动**未提交**，保留在 worktree
`.worktrees/layering-phase-two`。

> 状态：本地实现与门禁已完成并通过（§1）。**独立复核已完成**（计划 Task 14 Step 6）：
> 结论为 Spec issues，指出两处未记录的契约覆盖收缩；两处均已修复并用探针反证，见 §7。
> 本报告 §1–§6 是实现者自述，§7 记录复核发现与修复，两者不互相代替。

本报告只记录命令、统计与差异，不含连接串或凭据。

## 1. 结论

| 项 | 结果 |
| --- | --- |
| 完整套件 | 见 §3 |
| 三类 CI 分片 | unit / mysql / redis 互斥且并集为全集，零失败、零错误、零跳过 |
| import-linter | 78 contracts kept，0 broken（基线 68）。契约集合已逐条比对：**无放宽**；复核发现的两处收缩已修复（§7） |
| Node 合同 | 10 desktop chat contracts passed |
| Ruff 增量 | 新增 0（带路径映射比较，退出码 0）。**未刷新基线** |
| 冻结契约 | `routes`/`openapi`/`errors` 逐字一致；`metadata` 的 25 张表 schema 签名逐字一致 |
| 测试用例数 | `--collect-only` 714 → 720（只增不减：4 条布局验收 + 1 条 original 布局回归 + 1 条原生演示资源） |
| legacy 隔离 | 仅组合根与三个过渡路由引用 `legacy.*` |
| 独立复核 | 已完成，Spec issues → 两处契约收缩已修复并反证（§7） |

## 2. 实际改动

175 个文件、1062 插入 / 1048 删除（约 6 行/文件，与"只改 import"一致）：

| 类型 | 数量 | 说明 |
| --- | --- | --- |
| 重命名 | 94 | 包搬迁与文件改名 |
| 就地修改 | 68 | import 重写与少量必要的结构性修正 |
| 新增 | 8 | 5 个分组 `__init__.py`、`api/middleware.py`、`api/contracts/{__init__,auth}.py` |
| 删除 | 6 | `db/{__init__,base}.py`、`commerce_orchestrator/{model,utils}.py`、`api/routes/identity.py`、`api/routes/commerce_read.py` |

### 2.1 布局映射

```
catalog|pricing|inventory|cart|checkout|identity  → modules/
shopping_session → modules/sessions     commerce_events → modules/events
sale_facts → modules/sale_facts         storefront → application/storefront
commerce_orchestrator → application/shopping（service.py → orchestrator.py）
db/session.py → infra/db.py             commerce_orchestrator/llm_adapter.py → infra/llm.py
checkout/gateway.py → infra/checkout_gateway.py
agents|orchestrator|services|infrastructure → legacy/
models/schemas.py → legacy/models/      catalog/legacy_adapter.py → legacy/catalog_adapter.py
api/runtime.py → api/deps.py            api/*.py → api/routes/*.py
api/identity.py → api/middleware.py（HTTP 装配）+ api/routes/auth.py（主体依赖）
api/commerce_read.py → 并入 api/routes/catalog.py（保留两个 APIRouter 以维持路由顺序）
models/commerce.py → api/contracts/commerce.py（Turn DTO 归 application.shopping.contracts）
```

### 2.2 搬迁的纯净性

搬迁由带预检的 codemod 执行（`.artifacts/layering/task-14/migrate.py`），537 处 import、
105 个文件、0 处无法映射。98 个生产模块导入冒烟全部通过。除 import 行与上表列出的结构性
改动外，diff 中无其它代码变更。

## 3. 门禁

命令使用与第一阶段相同的参数，产物在 `.artifacts/layering/task-14/`（已 gitignore）。

```
python -m pytest tests/ -q --junitxml=.artifacts/layering/task-14/final/results.xml \
    --layering-artifacts=.artifacts/layering/task-14/final
python -m pytest tests/ -q -m 'not mysql and not redis' --junitxml=.../unit/results.xml
python -m pytest tests/ -q -m 'mysql and not redis'     --junitxml=.../mysql/results.xml
python -m pytest tests/ -q -m redis                     --junitxml=.../redis/results.xml
lint-imports --config .importlinter.stage.toml --no-cache
node tests/run_demo_contracts.cjs
ruff check . --output-format json   # 再经 tests.layering_reports lint 带路径映射比较
```

| 门禁 | 基线（8779cf5） | Task 14 | 说明 |
| --- | --- | --- | --- |
| 完整套件 | 714 passed，零失败/错误/跳过 | **720 passed，零失败/错误/跳过** | +6：4 条布局验收 + 1 条 original 布局回归 + 1 条原生演示资源（复核 M-2） |
| unit | 534 | **539**，零跳过 | |
| mysql | 151 | **151**，零跳过 | 数字与基线一致 |
| redis | 29 | **29**，零跳过 | 数字与基线一致 |
| import-linter | 68 kept / 0 broken | **78 kept / 0 broken** | 契约增加；例外从 9 降到 7 |
| Node 合同 | 10 passed | **10 passed** | |
| Ruff 增量 | 比较退出 0 | **新增 0，退出 0** | 未刷新基线 |
| 冻结契约 | — | routes/openapi/errors 逐字一致；metadata 25 张表签名逐字一致 | |

三片互斥且并集恰为全集（`unit ∪ mysql ∪ redis == full`，两两无交集）。

四条最能说明「搬迁未改变行为」的证据：

1. `metadata.json` 的 `tables` 部分——25 张表的列、类型、可空性、默认值、索引、外键——
   **逐字未变**（只有 `modules` 名字清单随布局更新）。
2. `routes.json` 的 33 条路由（含保留的 5 条 legacy 路由）与 `openapi.json` 逐字未变。
3. `errors.json` 记录的各端点错误状态码与响应体逐字未变。
4. 175 个文件的 diff 为 1062 插入 / 1048 删除，约 6 行/文件，与「只改 import」一致。

## 4. 必须记录的偏差与判断

| # | 事项 | 处置与理由 |
| --- | --- | --- |
| D-1 | `tests/test_layering_layout.py` 在搬迁**之后**补齐 | 未遵守计划的先红后绿顺序。断言本身与 grouped 布局一一对应（公开入口可导入、应用层不依赖 api/infra/legacy、旧路由仍在、旧平铺包已消失），已单独执行通过 |
| D-2 | `tests/fixtures/layering/metadata.json` 的 `modules` 字段被更新 | 该字段是"导入了哪些模块"的名字清单，随布局必然变化。**`tables` 部分（25 张表的 schema 签名）逐字未改**，已在更新前用脚本断言 |
| D-3 | `tests/test_shared_primitives.py` 删除 3 条别名断言 | 均为「兼容壳 is 真身」的同一性断言，其主语（`db/`、`commerce_orchestrator/utils.py`）按设计已删除，断言退化为自反。**全部行为断言保留**：metadata 唯一性与全模型共用、Principal 键值向量、request_hash 摘要值、UTC 往返、new_id v4、DomainError。`TimestampMixin` 由 15 个模型使用、其列由冻结契约覆盖 |
| D-4 | `pyproject.toml` 的 `[tool.layering] layout` 改为 `grouped` | 架构检查器据此选择布局表；不改则 `grouped` 无法校验 |
| D-5 | 修复 32 条 lint 告警 | 搬迁使 import 行变长/变序，导致 (路径, 规则, 行文本哈希) 三元组在基线中失配。按"搬迁不改变出现次数"的要求逐条修复（换行、删无用 import、中途 import 归位、`sys.path` 模式注明），**未刷新 lint 基线**，历史告警保持原样 |
| D-6 | `api/contracts/__init__.py` 由控制者补建 | 缺它时 grimp 看不见 `api.contracts.*`，会少两条 ORM 契约（75 → 78） |
| D-7 | `api/routes/demo.py` 的 `demo_dir` 改为组合根必传 | 搬迁后 `Path(__file__).resolve().parents[1]` 指向不存在的 `api/demo/`，演示页会 404。按设计 §6.3「由组合根配置绝对位置，路由不猜文件深度」修复，7 个调用点全部验证 |
| D-8 | Ruff `F401` 对 pytest fixture 导入是误报 | 自动修复删除了 `identity_key_files`，导致 `tests/test_commerce_{demo,auth_api}.py` fixture 解析失败（4+1 ERROR）。改用仓库既有的显式再导出写法 `identity_key_files as identity_key_files`（同 `tests/test_commerce_e2e.py`）——**不能用 `# noqa: F401`**：那会让每个以该名作 fixture 形参的测试函数触发 `F811`。教训：本仓库不要对测试文件跑 `ruff --fix --select F401` |
| D-9 | **独立复核发现的两处契约覆盖收缩，已修复** | 见 §7。属实现缺陷而非设计取舍，修复后已用探针反证 |
| D-10 | `tests/test_kernel_contracts.py` 删除 2 条 Turn 别名一致性断言未在原报告中记录 | 该断言的主语 `models.commerce` 的 Turn 重导出已按设计移除，属过时断言而非弱化；`openapi.json` 逐字一致已覆盖线上形状。此处补记 |

## 5. 未验证的边界

- **云端 GitHub Actions 未执行**；本报告的验收全部来自本地独立测试服务。
- 生产级 LLM、真实支付、物流、宿主结账系统未接入，不在本任务范围。
- `legacy/` 保留的旧推荐栈只验证了「路由仍注册、测试仍通过」，其已知的实验实例分裂问题
  按计划不在结构迁移中修复。
- Task 15（`ecom/` 包根与 factory 启动）、Task 16（tests/tools 分类）未执行；
  Task 17–18（规则归一 R）仍需独立批准。

## 6. 回滚

本任务不新增表/列，不改变 ID 编码、UTC 类型、JSON 落库形状或幂等请求哈希
（25 张表 schema 签名逐字一致即为证据），因此**无需数据回迁**。
回滚需同时恢复：import、启动入口、Alembic 模型注册、静态资源定位、架构配置与 lint 基线引用。
## 7. 独立复核发现与修复

复核者不参与实现，按设计 §6.1 逐条核对契约集合、负例覆盖、冻结契约与搬迁纯净性。
结论为 **Spec issues（需修复）**：行为零变化、搬迁纯净、覆盖无损失、门禁数字属实，
但架构契约有 **3 处未记录的覆盖收缩**。以下为发现与处置。

### 7.1 生成器 `visible` 过滤丢弃顶层命名空间包（已修复）

`tests/layering_rules.py` 的 `_contracts()` 用

```python
visible = {m for m, path in modules.items() if path is not None and _graph_visible(m, modules)}
```

过滤 source/target。`data/`、`migrations/`、`scripts/` 无 `__init__.py`，在 inventory 中 `path is None`，
于是被整体剔除——**与 `_graph_visible` 自己的 docstring 直接矛盾**（该函数明确写着「顶层命名空间包
（`data`/`scripts`/`migrations`）不受影响」，且 `count == 1` 分支就是为它们准备的）。
后果：`shared 不得依赖配置或业务包` 与 `kernel 只允许基础 schemas 与 shared` 两条契约的
`forbidden_modules` 少了这三项，`lint-imports` 不再拦这类依赖。

基线（`original` 布局）用的是 `& modules.keys()`，没有这层过滤——**这是本次重写引入的回归，不是设计变更**。

**修复**：去掉自相矛盾的 `path is not None`，只保留 `_graph_visible`。
`shared` 契约恢复为 10 项、`kernel` 为 9 项，三项均在其中。

**反证**（探针：向 `shared/__init__.py` 追加 `import data.download_amazon_metadata`）：

```
修复前：api.routes.health -> legacy.infrastructure 之外无报错，78 kept / 0 broken
修复后：shared 不得依赖配置或业务包 BROKEN —— "shared is not allowed to import data"
```

### 7.2 `protected` 契约丢失 `legacy.infrastructure`（已修复）

`GROUPED_TABLES.adapter_prefixes` 被置为 `frozenset()`，而 `infrastructure` 已迁至
`legacy.infrastructure`，导致 `具体适配器仅组合根与精确兼容入口可以直接导入`
的 `protected_modules` 从四项变成三项，旧栈的 MySQL/Redis/Milvus 连接封装失去保护。

**修复**：`adapter_prefixes=frozenset({'legacy.infrastructure'})`，`protected_modules` 恢复为四项。

**反证**（探针：向 `api/routes/health.py` 追加 `from legacy.infrastructure import redis_client`）：

```
修复后：Illegal imports of protected package legacy.infrastructure:
        - api.routes.health -> legacy.infrastructure.redis_client (l.13)
```

两处修复后 `lint-imports` 仍为 **78 kept / 0 broken**；探针还原后合约集合不变。
架构测试 237 passed、单元 539 passed。

### 7.3 其余复核结论

- **未发现**行为变化、事务/锁/幂等语义改动、覆盖损失或越权提交。
  四个关键事务文件（`actions.py`、`cart/service.py`、`checkout/service.py`、`sessions/service.py`）
  剥离 import 后 AST 比较**零差异**。
- 测试用例 714 → 719，73 个消失的 node id 全部能一一对应到改名后的 id。
- `test_rejects_import_bypasses` 参数仍是 25 条，断言为 `assert any(... in issue ...)` 非恒真。
- `metadata.json` 去掉 `modules` 字段后与基线**完全相等**。
- **M-1**：`tests/test_kernel_contracts.py` 删除的 2 条 Turn 别名断言已补记为 D-10。
- **M-2**：`/demo/commerce.js` 与 `/demo/commerce.css` 此前无测试覆盖（复核者实测均 200）。
  已新增 `test_native_demo_assets_come_from_composition_root`，专门锁定这两条路径
  与「无 React 构建时回退到原生页」的分支。

