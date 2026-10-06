# 第一阶段分层验收报告

日期：2026-10-06。范围：Task 1–13 的 original 布局；本报告记录本地验收结果，不代表云端 GitHub Actions 已执行或审批已通过。

## 主目录同步验收

阶段验收后，用户明确要求将当前实现合并回主目录。已将隔离worktree的130个差异文件同步到 `/Users/anon/workspace/multi-agent-ecommerce-system`，保持主目录的 `python` 分支及未提交状态；原AGENTS、设计内容和本地配置保留，实施计划同步了已完成状态和此前已审查的两处示例修正。没有git add/commit、远程操作、清理worktree或推进D5。

主目录使用已有worktree虚拟环境和专用测试服务重新运行完整套件：**714 passed，150.57s，零失败、错误、跳过**。真实CLI68 kept/0 broken、JS10通过、新增lint0；原节点与四冻结合同保持，136份受验收源码/配置指纹与最后修复版一致。已确认实际加载的应用路径来自主目录，而非worktree。

本次日志、JUnit、manifest及合同位于主目录 ignored 的 `.artifacts/layering/main-sync-final/`；原文件备份和130文件同步清单在worktree ignored 的 `.artifacts/layering/main-sync/`。阶段验收时的隔离状态记录保留为历史，当前实现已同步；本节为同步后的最新状态。

用户随后授权提交当前阶段。提交范围是130个同步文件加设计文档；原有AGENTS修改保留为未提交，忽略的本地环境、日志和worktree不纳入提交。该授权不包括远程推送或D5/R/L后续实施。

## 最终审查与控制器验收

逐项独立审查和整体阶段审查已完成；整体审查唯一 Important 的反射函数别名漏检经定点修复及独立复核解决，无未处理 Critical/Important/Minor。最后复核另做函数内别名及后覆盖的六个定向探针，全部通过。技术审查结论不等于提交、合并或后续阶段授权。

控制器在最终修复、复核完成后冻结源码，重新串行执行完整套件和三类门禁：

| 门禁 | 最终结果 | 时间 |
| --- | --- | --- |
| 完整套件 | 714 passed，零失败、错误、跳过 | 155.84s |
| unit | 534 passed，180 deselected | 8.62s |
| mysql | 151 passed，563 deselected | 117.68s |
| redis | 29 passed，685 deselected | 23.39s |
| import-linter | 68 kept，0 broken | 退出码0 |
| Node 合同 | 10 passed | 退出码0 |
| Ruff 增量 | 新增0，保留历史告警 | 比较退出码0 |

四份 JUnit 均通过 zero-skips；三组节点互斥，并集恰为714。`compare-ci` 通过，原226节点保留；对完整基线的 `compare_results(..., {}, {})` 返回空列表。四冻结合同逐JSON相等。最终门禁结束后逐一核对136份源码/配置内容指纹，与最后修复指纹全部一致。

最终证据在 `.artifacts/layering/phase-final/`：`results.xml`、`manifest.json`、四合同、`full-suite.log`、`unit/`、`mysql/`、`redis/`、对应日志、`imports.log`、`lint-current.json`、`verification-summary.json`。命令使用下文相同参数，产物目录换为 `.artifacts/layering/phase-final`；完整验证脚本是 ignored 的 `.superpowers/sdd/2026-10-06-layering-implementation/verify_phase_final.py`。完整套件外层 shell 一度使用 zsh 只读变量 `status`，导致包装命令退出1；pytest本身已完整通过，后续直接读取JUnit及日志确认，不将该包装错误当作测试RED，也未重造报告。

HEAD仍为 `9db6b97786a3621c4943662da669e070f65119ff`，暂存区为空。原工作区仍仅用户既有AGENTS、设计和计划三项修改；实施代码保留在隔离 worktree `.worktrees/layering-implementation`，没有复制回原工作区。独立MySQL/Redis容器保持healthy。

## 审查修复后状态

Task 13 首轮审查发现普通静态别名的两个遗漏：带值 `AnnAssign` 的 ORM 重导出，以及模块普通/注解别名后的私有属性和 getattr 访问。fix-1 已统一符号与模块的来源解析，新增 13 个用例（8 个拒绝绕过、5 个合法 DTO/公共服务别名正例）。修复后定向 166 passed，零失败、错误和跳过；真实 CLI 68 kept，JS 10 passed，新增 lint 0，四冻结合同仍一致。

fix-1 复审确认 I1/I2 的具名场景已解决，同时发现 I3：后续重新赋值覆盖最终来源，可能抹去先前私有属性或模块反射的违规证据。fix-2 将绑定改为保守来源集合，Assign/AnnAssign 追加来源，不以末次赋值撤销历史模块身份；属性、反射和重导出共同消费该集合。本轮新增 10 个顺序用例，涵盖两条最小复现、注解赋值、函数内链式别名，以及合法公共访问后的重新绑定。

fix-2 当轮定向结果为 176 passed，零失败、错误和跳过，包含保留的 65 个架构用例与本轮 10 个用例。真实 CLI 68 kept，JS 10 passed，新增 lint 0，四冻结合同一致。该保守策略避免构建通用控制流引擎；同一名字曾持有模块身份时，后续重新绑定不会使审计放弃该来源。

整体阶段审查另发现反射函数本身的别名漏检。phase-fix-1 将 `getattr`、`setattr`、`vars` 作为 `builtins` 函数身份接入相同来源集合，识别裸名、builtins 导入别名、模块属性、普通/注解与链式赋值，并保留后续覆盖前的来源；只有目标对象来自项目模块时才拒绝反射。新增 45 个用例，覆盖 21 个漏检变体、3 个原本受控的裸调用及 21 个合法非项目对象调用。

最新 phase-fix-1 定向结果为 **221 passed，零失败、错误和跳过**；120 个架构规则用例全部保留并通过。真实 CLI 68 kept、JS 10 passed、新增 lint 0、四冻结合同一致。实现只改架构检查器、测试与报告，没有业务代码修改，也没有全局禁用反射。

**下表的646个完整测试与三类门禁是审查修复前证据，不代表三轮修复后全量结果。** 修复阶段未重复完整套件或三类服务门禁；最终独立复审和控制器714全套见报告首节。历史证据分别位于 `.artifacts/layering/task-13/` 的 `fix-1/`、`fix-2/`、`phase-fix-1/`，原证据未覆盖。

## 首轮审查前验收结果

| 门禁 | 结果 | 时间 |
| --- | --- | --- |
| 最终定向架构、kernel 与冻结合同 | 153 passed | 6.07s |
| 完整测试套件，实际执行一次 | 646 passed，0 failed/error/skipped | 152.75s |
| unit：`not mysql and not redis` | 466 passed，180 deselected，零跳过 | 8.17s |
| mysql：`mysql and not redis` | 151 passed，495 deselected，零跳过 | 115.62s |
| redis：`redis`，同时提供独立 MySQL | 29 passed，617 deselected，零跳过 | 21.81s |
| 真实 import-linter CLI | 68 kept，0 broken | 退出码 0 |
| Node demo 合同 | 10 passed | 退出码 0 |
| Ruff 增量门禁 | 新增 0；现存 894，冻结基线 1170 | 比较退出码 0 |
| `git diff --check` | 通过 | 退出码 0 |

三份门禁的 JUnit 与 manifest 一致，集合并集恰好为完整套件的 646 个节点，无重复或缺失。原 226 个冻结节点全部保留；整个第一阶段新增 420 个通过节点，其中本任务新增 52 个架构用例。`compare-ci` 通过，`compare_results(..., {}, {})` 返回空列表；结构阶段没有批准的路径映射或行为退出项。

四份冻结合同与本次捕获结果逐 JSON 相等，未重新生成冻结文件：`routes.json` 保留 33 条路由记录，`openapi.json` 保留 29 个路径，`metadata.json` 保留 25 张表及其约束、索引等结构，`errors.json` 保留 7 个响应样本。隐藏路由记录、API 形状及数据库结构未发生合同差异。

## 架构门禁与取舍

采用真实依赖图 CLI 加 AST 检查。CLI 负责 forbidden/protected 图约束；AST 负责显式公共符号、私有与 ORM 重导出、函数内和 TYPE_CHECKING 导入、相对导入、动态导入与模块反射，以及精确角色清单。仅使用 import-linter 不能判定符号是否来自 `__all__`，仅使用 AST 又缺少已有图工具的独立验证，因此保留两者。代价是必须维护角色清单和精确例外；CI 同时检查实际目录、生成配置与规则是否一致，避免新增模块漏登。

original 清单包含 134 个模块节点、25 个顶层入口，含 namespace 中间节点 `migrations.versions`。角色为 legacy 15、API 15、组合根 2、业务 46、兼容入口 4、具体基础设施 7、应用 15、配置 2、工具 16、kernel 5、shared 7。`main.py` 是单文件模块，import-linter 2.15 的 `root_packages` 只接受目录包，因此 CLI 登记另外 24 个顶层包，AST 完整扫描 `main`，不把它从验收清单删除。

Task 2–12 的已修复约束保留并扩展到全部实际现代模块：shared 不依赖配置或业务；kernel 只使用基础 schemas 与 shared；基础 schemas 不反向依赖 kernel；模块与应用不依赖 HTTP、具体适配器或旧栈；cart、checkout、sessions 的用例协调限制继续生效。跨模块 ORM 契约使用 `allow_indirect_imports=true`，允许公共 service 间接读取自身 models；上层禁止依赖继续检查间接路径。AST 额外追踪包初始化和公共 schemas 的依赖，避免精确 schema 例外掩盖非法上层依赖。

20 个明确的公共入口补充 `__all__`，不重写业务实现。扫描发现 `api.auth` 原先直接访问 `identity.passwords.PasswordPolicyError`，由控制器交回 Task 11 实现者独立补修与审查；现在通过 `identity.service` 公开同一异常对象，HTTP 错误合同保持不变。

例外按文件、模块或符号登记，不使用包通配 ignore：

- 应用组合根为 `main` 与 `api.runtime`；暂存适配器为 `db.session`、`checkout.gateway`、`commerce_orchestrator.llm_adapter`。
- 旧入口保留 `db -> db.session`、`commerce_orchestrator.model -> commerce_orchestrator.llm_adapter`、`catalog.legacy_adapter -> models.schemas`；`identity.types` 对 `shared.principal._length_prefixed` 的旧符号别名单独登记。
- 过渡 API 引用为 `api.commerce -> models.commerce` 与 `api.recommendations -> models.schemas`。
- Alembic 的 `migrations.env` 仅登记八个实际 ORM 模块：catalog、pricing、inventory、identity、shopping_session、cart、checkout、commerce_events。
- 独立工具的精确入口包括 `data.import_amazon_metadata -> db.session/catalog.models/catalog.seed/inventory.models/inventory.seed/pricing.seed`，以及 `scripts.reset_demo_password -> db.session/identity.passwords`。这些不是 HTTP 应用的普遍例外。

## 实际执行命令

工作目录为批准的隔离 worktree。所有 Python 命令先读取 ignored 的测试配置，使用该 worktree 的虚拟环境、独立 MySQL 与隔离 Redis。未打印连接配置、未调用真实 LLM、未执行 FLUSHDB；MySQL 测试进程串行运行，运行期间未中断 DDL 或修改源码。

```sh
source .superpowers/sdd/2026-10-06-layering-implementation/test.env
python -m pytest tests/test_layering.py tests/test_layering_rules.py tests/test_kernel_contracts.py tests/test_layering_baseline.py -q --tb=short
python -m pytest tests/ -q --tb=short --require-integration-services --junitxml=.artifacts/layering/task-13/results.xml --layering-artifacts=.artifacts/layering/task-13
python -m tests.layering_reports zero-skips .artifacts/layering/task-13/results.xml
python -m pytest tests/ -q --tb=short -m 'not mysql and not redis' --require-integration-services --junitxml=.artifacts/layering/task-13/unit/results.xml --layering-artifacts=.artifacts/layering/task-13/unit
python -m pytest tests/ -q --tb=short -m 'mysql and not redis' --require-integration-services --junitxml=.artifacts/layering/task-13/mysql/results.xml --layering-artifacts=.artifacts/layering/task-13/mysql
python -m pytest tests/ -q --tb=short -m redis --require-integration-services --junitxml=.artifacts/layering/task-13/redis/results.xml --layering-artifacts=.artifacts/layering/task-13/redis
python -m tests.layering_reports zero-skips .artifacts/layering/task-13/unit/results.xml
python -m tests.layering_reports zero-skips .artifacts/layering/task-13/mysql/results.xml
python -m tests.layering_reports zero-skips .artifacts/layering/task-13/redis/results.xml
python -m tests.layering_reports compare-ci --baseline tests/fixtures/layering --unit .artifacts/layering/task-13/unit --mysql .artifacts/layering/task-13/mysql --redis .artifacts/layering/task-13/redis
lint-imports --config .importlinter.stage.toml --no-cache
node tests/run_demo_contracts.cjs
ruff check . --output-format json > .artifacts/layering/task-13/lint-current.json
python -m tests.layering_reports lint --baseline tests/fixtures/layering/lint-baseline.json --current .artifacts/layering/task-13/lint-current.json
git diff --check
```

Ruff 原始退出码为 1，表示已有告警；执行包装明确拒绝大于 1 的工具错误，不能将工具失败当作历史告警。结果比较还实际调用 `compare_results(Path('.artifacts/layering/baseline'), Path('.artifacts/layering/task-13'), {}, {})`，返回 `[]`。

本地版本：Python 3.12.13、Node v26.3.0、import-linter 2.15、Ruff 0.16.10。CI 配置仍使用 Python 3.12 与 Node 22；本地 Node 满足项目的 `>=22.12` 下限，但没有据此宣称云端相同环境已通过。

## 证据、限制与审批止点

首轮日志、四份合同、JUnit、manifest、清单签名及 `verification-summary.json` 位于 ignored 的 `.artifacts/layering/task-13/`；各门禁分别位于其 `unit/`、`mysql/`、`redis/` 子目录。该轮结束时曾核对 136 份源码与配置签名不变；后续修复均修改了检查器与新测试文件，因此原签名及摘要仅作为相应历史证据，不能用于断言当前源码未变。最新内容指纹另存 `phase-fix-1/source-signature.json`，其验证范围是本轮定向检查。

AST 是项目静态架构门禁，不是任意 Python 程序的安全沙箱；复杂运行期元编程仍需要代码审查，保守历史来源分析也可能报告复杂重绑定的潜在违规。当前只启用 original 布局；grouped、packaged 及未知布局明确失败，不伪造尚未存在的包，也不自动搬迁。此次未执行云端 GitHub Actions或真实生产LLM/支付/物流，不把本地验证外推到这些环境；未创建提交、暂存或合并。独立逐项审查与整体阶段审查已完成，所有阻塞发现均已修复和复核。

**第一阶段Task1–13已完成并验收，到D5停止点暂停。D5、R、L尚未批准；Task14及后续搬迁、规则变更和旧栈退役均未执行。保留未提交worktree与验收证据，等待独立审批。**
