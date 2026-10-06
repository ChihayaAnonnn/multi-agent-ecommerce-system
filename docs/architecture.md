# 系统架构

> 适用版本：分层重构 Task 15 之后的 `packaged` 布局（唯一包根 `ecom/`，源码 factory 入口）。
> 本文描述**当前源码**。旧推荐栈按 D1/D2 保留在 `ecom/legacy/`，见文末「保留的旧栈」。
> 分层设计与决策依据见 [`layering-design.md`](layering-design.md)。

## 1. 系统概览

本系统是一个**模块化单体**的电商后端：HTTP 接口 + 可信购物 Agent + 领域模块 + 基础设施适配器。
真实交易事实（目录、价格、库存、购物车、结账交接）来自 MySQL，由领域模块持有；
对话编排只读取已核验事实，不臆造商品、价格或库存。

```
客户端
  │
  ▼
ecom.api              接口层：路由、依赖装配、错误映射、中间件
  │
  ▼
ecom.application      用例编排
  ├── shopping/       购物 Agent：LangGraph 图 + 动作用例 + 事实读取
  └── storefront/     商品展厅只读投影
  │
  ▼
ecom.modules          业务模块：catalog pricing inventory cart checkout identity sessions events sale_facts
  │
  ▼
ecom.kernel  ecom.shared  共享契约与纯规则 / 零业务依赖原语
  │
  ▼
ecom.infra            适配器：MySQL 引擎、Redis、LLM、宿主结账网关
```

`ecom/main.py` 是组合根：只有它可以构造 `infra` 的适配器并注入其余各层；应用由
`create_app() -> FastAPI` 工厂构造，运行时（Runtime）只在 lifespan 中创建并关闭。
从仓库根启动：`python -m uvicorn ecom.main:create_app --factory --host 0.0.0.0 --port 8000`。

## 2. 分层与依赖规则

| 分组 | 职责 | 一句话判据 |
| --- | --- | --- |
| `ecom.api` | HTTP 接口层 | 只有它认识 `Request`/`Response`/HTTP 状态码 |
| `ecom.application` | 用例编排 | 感知会话、轮次、确认门禁、多模块拼装 |
| `ecom.modules` | 限界上下文 | 不感知 HTTP，不承担对话意图判断 |
| `ecom.kernel` | 跨模块共享契约与纯规则 | 无 I/O；只引用 catalog/pricing/inventory 的 `schemas` |
| `ecom.shared` | 零业务依赖原语 | 不 import 本工程任何其他分组 |
| `ecom.infra` | 适配器 | 只被组合根构造 |

依赖方向 `api → application → modules → shared`；`kernel` 与 `modules` 之间存在一条受控窄环
（cart 需要 `kernel.quotes`，`kernel.quotes` 需要基础模块的 DTO），由契约钉死为「只准碰 `schemas.py`」。

跨模块只能引用对方的 `service.py` 公共方法、`schemas.py` 与公开的 `ports.py`/`types.py`；
禁止直接 import 其他模块的 `models.py`。边界由 `pyproject.toml` 的 `[tool.importlinter]`
（真实 CLI 检查，`lint-imports --config pyproject.toml --no-cache`）与
`tests/test_layering_rules.py`（AST 白名单与正负例）双重强制。

## 3. 购物 Agent 的一次轮次

```
用户消息
  │
  ▼
parse_intent      解析意图与约束（模型只能提软偏好，硬条件由代码解析）
  │
  ▼
validate_route    路由校验；订单/售后暂不支持，缺关键输入转澄清
  │
  ├─ shop/selection ─► discover ─► quote_filter ─► rank_explain
  ├─ compare        ─► compare
  ├─ cart_read      ─► read_cart
  ├─ cart_mutation  ─► propose_cart_action
  └─ checkout       ─► preview_checkout
  │
  ▼
finalize          组装 TurnResponse
```

**关键约束**：

- 目录扫描（`discover`）只返回已核验可购买的 SKU；缺价、库存未知、非 CNY 一律不算可买。
- 写操作（加购、改数量、移除、结账交接）**不由对话直接执行**：Agent 只生成待确认动作，
  用户在可见摘要上确认后才在单个事务内写车。
- 轮次通过租约（lease）串行化：同一会话的并发轮次会被拒绝，而不是并发写。
- 幂等：`turn_id` 与请求哈希决定回放；确认动作带 `idempotency_key`。

## 4. 数据流与事务边界

- **单模块 CRUD**：模块自管事务（`async with factory(), db.begin()`）。
- **跨模块用例**（提案/确认/结账）：由 `ecom/application/shopping/actions.py` 建立事务，
  被调用的模块公开 `*_in_transaction` 操作接收同一个 `AsyncSession`，不自行提交或回滚。
- **结账交接**分两段事务 + 锁外宿主调用：第一段校验门禁、保存 `preparing` 并消费动作；
  提交后调用宿主网关（同一 handoff ID 作幂等键）；第二段保存 `ready`/`failed`。
  网络调用绝不持锁。
- **锁顺序**：会话 → 购物车与动作 → 目录 → CNY 价格 → 库存。库存是钱，顺序不能改。

## 5. 身份与主体

`ecom/modules/identity/` 只回答「请求者是谁」。购物会话、购物车、交接记录的归属
一律按 `Principal.principal_key` 校验，路由不得读取请求体里的 `user_id`。

访问令牌为 RS256 Bearer；刷新令牌放在 `__Host-` + `Secure` + `HttpOnly` + `SameSite=Lax` 的 Cookie 中。
身份依赖不可用时受保护端点返回 503，**绝不降级为匿名或固定主体**。

## 6. 保留的旧栈（按 D1/D2）

`ecom/legacy/` 保留旧推荐栈与实验/指标，**不是当前主线**：

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| `ecom/legacy/agents/` | 用户画像、商品推荐、营销文案、库存四个演示 Agent | 依赖硬编码样例商品，与真实目录无关 |
| `ecom/legacy/orchestrator/` | 旧推荐管线（Supervisor 与 LangGraph 两份实现） | 两条路径都注册；已知存在实验实例分裂问题，未修 |
| `ecom/legacy/models/` | 旧展示契约（浮点金额） | 仅为旧接口兼容 |
| `ecom/legacy/services/` | A/B 引擎、指标、特征存储 | 进程内状态；`feature_store` 无生产调用方 |
| `ecom/legacy/infrastructure/` | 旧的 MySQL/Redis/Milvus 连接封装 | 无生产调用方 |
| `ecom/legacy/catalog_adapter.py` | 领域对象 → 旧展示模型的单向适配 | 旧接口过渡用 |

对应的 `/api/v1/recommend`、`/api/v1/recommend/graph`、`/api/v1/experiments`、`/api/v1/metrics`
路由保持注册且行为不变。新业务层不得依赖 `ecom.legacy`，只有组合根（`ecom/main.py`、`ecom/api/deps.py`）
与三个过渡路由例外，由分层契约强制。

## 7. 未验证的边界

以下内容**不在本仓库的验证范围内**，不要在文档或对外说明中当作已具备的能力：

- 云端 CI 未执行；本地三类门禁为验收依据。
- 生产级 LLM、真实支付、真实物流、真实宿主结账系统均未接入。
  结账只做「交接」，不创建订单、不扣款。
- Milvus 向量召回、离线特征、订单与售后均未实现。
