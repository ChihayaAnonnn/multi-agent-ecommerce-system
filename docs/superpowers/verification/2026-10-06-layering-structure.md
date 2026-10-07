# 结构阶段验收报告（Task 14–16）

日期：2026-10-06。范围：设计 §3 的 `grouped` 与 `packaged` 布局，即实施计划 **Task 14–16**。
基线：提交 `8779cf5`（Task 1–13 的 `original` 布局）。

> **本报告只覆盖结构迁移。** 规则归一（Task 17–18，R）与旧栈退役（Task 19，L）**均未执行**，
> 见 §6。报告中的每个数字都来自本地独立测试服务，**云端 CI 未运行**。

| 提交 | 内容 |
| --- | --- |
| `2971438` | Task 14：顶层分组（`modules/ application/ infra/ legacy/`） |
| `4218748` | 修复布局测试对陈旧字节码的假失败 |
| `3939f15` | Task 15：`ecom/` 唯一包根与 `create_app()` factory |
| `69a5d6f` | Task 16：tests 按依赖分类、工具归位、CI 收尾 |

## 1. 设计问题（P1–P5）的处置

| 编号 | 问题 | 现状 |
| --- | --- | --- |
| P1 | 顶层混装多种角色 | **已解决**：20+ 平铺包 → 唯一 `ecom/`，内部六组（`api/ application/ modules/ kernel/ shared/ infra/`）+ `legacy/` |
| P2 | 从未声明依赖方向，存在反向依赖 | **已解决**：反向依赖在 Task 3–11 消除；Task 14–16 用 `.importlinter` 契约（82 条）与 AST 白名单双重强制 |
| P3 | 没有唯一包根，靠 `sys.path.insert` 兜底 | **已解决**：`sys.path.insert` 已删除；仓库根不再有 `.py`；入口为 `python -m uvicorn ecom.main:create_app --factory` |
| P4 | 同一业务规则多份拷贝 | **未解决**（属 R）：可购买性判定仍是三份、硬条件匹配两份。结构迁移刻意保留差异，见 §6 |
| P5 | 依赖装配靠构造后回填 | **已解决**：`create_app()` 构造即完整；Runtime 只在 lifespan 创建、`finally` 关闭；无构造后赋值 |

## 2. 八项评审问题的处置

| 评审问题 | 状态 |
| --- | --- |
| Turn DTO 归 API 导致反向依赖 | 已解决：Turn DTO 归 `ecom.application.shopping.contracts`，API 复用不重导出 |
| kernel.quote_many 与服务/异常归属冲突 | 已解决：kernel 无 I/O；`SaleFactsReader` 归 `ecom.modules.sale_facts`；依赖错误归 `ecom.kernel.errors` |
| 跨模块 ORM 与动作外移缺事务设计 | 已解决：`*_in_transaction` 公共操作；四个关键事务文件剥离 import 后 **AST 零差异** |
| CI 布尔断言、间接访问误报、组合根约束遗漏 | 已解决：CLI 退出码比较、`allow_indirect_imports` 区分、`protected` 契约限制适配器只被组合根导入 |
| 不改行为与错误形状/接口退役冲突 | 已解决：错误体与状态码冻结契约逐字一致；L 未执行 |
| ecom 未建立就检查、skip 数造成假绿 | 已解决：`--collect-only` node id 逐个映射；三类分片零跳过 |
| 负库存判定与预算函数缺现价输入 | 未解决（属 R） |
| P5 只移动 Runtime 未解决回填 | 已解决，见 P5 |

## 3. 最终门禁

命令与第一阶段一致。产物在 `.artifacts/layering/`（已 gitignore）。

| 门禁 | 基线 `8779cf5` | 结构阶段 | 说明 |
| --- | --- | --- | --- |
| 完整套件 | 714 passed，零失败/错误/跳过 | **729 passed，零失败/错误/跳过** | +15 条新用例 |
| unit / mysql / redis | 534 / 151 / 29 | **549 / 151 / 29** | 三片互斥，并集恰为全集 |
| import-linter | 68 kept / 0 broken | **82 kept / 0 broken** | 契约只增不减 |
| Node 合同 | 10 passed | **10 passed** | |
| Ruff 增量 | — | **0** | 未刷新 lint 基线 |
| 冻结契约 | — | `routes`/`openapi`/`errors` 逐字未改；`metadata` 的 25 张表签名逐字未改 | |
| `alembic heads` | `0008_commerce_events` | **同一单头** | 无 schema 变化 |
| 入口 | `python main.py` | `create_app()` 产出 **33 条路由记录**（其中 29 条出现在 OpenAPI paths） | 与基线路径集合一致；两个口径不同，不能简写成「29 条路由」 |

**测试用例 714 → 729，只增不减；逐 `--collect-only` 比对，原有用例零丢失。**

## 4. 各任务中发现的缺陷与修复

结构迁移最容易"门禁全绿但已经坏了"，以下每条都是实际发生并被抓住的：

| 任务 | 缺陷 | 发现方式 | 处置 |
| --- | --- | --- | --- |
| 14 | `api/routes/demo.py` 的 `DEMO_DIR` 指向不存在的 `api/demo/`，演示页会 404 | 我复核路径计算 | 按设计 §6.3 改为组合根传绝对路径；补 `.js`/`.css` 两条此前无覆盖的用例 |
| 14 | 架构生成器 `visible` 过滤丢弃顶层命名空间包，`shared`/`kernel` 契约的 forbidden 集合静默少 3 项 | **独立复核（探针）** | 去掉自相矛盾的 `path is not None`；探针反证修复有效 |
| 14 | `protected` 契约丢失 `legacy.infrastructure` | **独立复核（探针）** | 补回 `adapter_prefixes`；探针反证 |
| 14 | Ruff `F401` 误删 pytest fixture 导入，破坏 2 个测试文件 | 我跑测试 | 改用 `name as name` 显式再导出；记录"不要对 tests 跑 `--fix --select F401`" |
| 14 | 布局测试对陈旧 `__pycache__` 假失败 | 我合并时踩到 | 改断言残留 `.py` 源文件，三种情形验证 |
| 15 | 无（契约 78 → 82，逐条比对无消失、无集合收缩） | 我逐条比对 | — |
| 16 | 契约中 `data`/`scripts` 消失 | 我逐条比对 | 确认是合并为 `tools` 的改名，`tools` 已在禁依赖集合内，探针验证 |

## 5. 回滚

结构迁移**不新增表/列、不改 ID 编码、UTC 类型、JSON 落库形状或幂等请求哈希**
（25 张表 schema 签名逐字一致即为证据），因此**无需数据回迁**。

回滚需同时恢复：import 路径、启动入口（Dockerfile/alembic/CI）、静态资源定位、架构配置与测试布局。
四个提交可按序 `git revert`；`4218748` 是纯测试修正，可独立回滚。

## 6. 未完成的范围

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| **R：规则归一（Task 17–18）** | **未执行，待批准** | 会把三份可购买性判定合并为一份，是**行为变更**：报价 API 将开始检查币种，购物车锁定路径将开始检查库存状态。结构迁移刻意保留了这三处差异 |
| **L：旧栈退役（Task 19）** | **本轮排除** | 按已确认的 D1/D2 保留旧推荐栈、实验与指标；路由、注册与测试全部保留 |

其余未验证边界：**云端 GitHub Actions 未执行**；生产级 LLM、真实支付、物流与宿主结账系统未接入；
`tools/amazon/*` 的真实下载与导入路径未端到端执行（仅 `--help` 与单元测试覆盖）。

## 7. 决策状态

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| D1 | 旧推荐栈保留为 legacy | 已确认并执行（`ecom/legacy/`） |
| D2 | 保留实验与指标 | 已确认并执行（`ecom/legacy/services/`） |
| D3 | `demo/` 保持顶层 | 已执行；位置由组合根按仓库根给出 |
| D4 | `application/` 不直接 import `infra` | 已执行，由契约强制 |
| D5 | 执行顶层分组与包根搬迁 | 已确认并完成（Task 14–16） |
