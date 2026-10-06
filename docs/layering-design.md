# 分层设计方案

> 状态：评审修订稿，待确认迁移范围；本文不是已经完成的实现。
> 范围：Python 后端（FastAPI + 领域模块 + 对话编排）。前端不重构；启动入口、迁移脚本、测试、工具和文档仅做必要的同步调整。
> 前置阅读：[`architecture.md`](architecture.md)（旧推荐栈）、[`prd-ecommerce-agent.md`](prd-ecommerce-agent.md) 第 2 节（历史能力盘点）、[`../ecommerce-agent-references/README.md`](../ecommerce-agent-references/README.md)。现状以当前源码为准，不能用历史 PRD 代替退役审批。

## 1. 目标与约束

### 1.1 要解决的问题

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| P1 | 顶层目录混装多种角色，看名字分不出层 | 业务领域、编排、基础设施、接口、工具全部平铺 |
| P2 | 从未声明依赖方向，已存在反向依赖 | `cart/service.py:8,16,17` → `commerce_orchestrator`；`shopping_session/service.py:10` → `models.commerce` |
| P3 | 没有唯一包根，靠运行期补丁兜底 | `main.py:10` 的 `sys.path.insert`；`data/import_amazon_metadata.py` 被测试当模块导入 |
| P4 | 同一业务规则多份拷贝 | 可购买性判定三份、硬条件匹配两份 |
| P5 | 依赖装配靠构造后回填 | `api/runtime.py:137,141` 的 `runtime.commerce.cart = ...` |

本次设计同时解决 P1–P5。其中 P2、P4 是正确性风险，P1、P3、P5 是可维护性风险。

### 1.2 约束

- **结构迁移不改业务行为**。保留现有路由前缀、请求/响应、HTTP 状态码、错误体、幂等与锁定语义。P4 的规则差异修正单列为行为变更提交，逐项记录前后差异（§4.2、§4.3），不能用更新测试期望掩盖兼容性变化。
- **旧栈退役不属于默认迁移范围**。删除推荐/实验接口、调整错误体均需独立确认、兼容性说明与验收；D1 未确认时保留现有调用链（§5.5、§9）。
- **测试必须有真实执行证据**。缺 `ECOM_TEST_DATABASE_URL` / `ECOM_TEST_REDIS_URL` 时集成测试会跳过。迁移前后记录测试清单、执行结果、跳过原因与路径映射；关键集成任务要求零跳过，不能只比较 skip 总数（§6.2）。
- **保持模块内聚**。8 个业务模块当前的 `models/schemas/service` 三段式形状统一、注释完整，不推倒重来。
- **可增量落地**。每一阶段独立可验证、可回滚。

### 1.3 不做的取舍

不引入纯净的 `domain/` 层（实体与规则完全脱离数据库）。理由见第 7 节。

## 2. 分层模型

采用**模块化单体（modular monolith）：边界分层、内部按限界上下文模块化**。

### 2.1 六个分组

| 分组 | 职责 | 一句话判据 |
| --- | --- | --- |
| `api/` | HTTP ↔ DTO 转换、鉴权依赖、错误映射 | 只有它认识 `Request`/`Response`/HTTP 状态码 |
| `application/` | 用例编排：对话轮次、Agent 图、只读投影 | 感知会话、轮次、确认门禁、多模块拼装 |
| `modules/` | 限界上下文及可复用的交易事实能力 | 不感知 HTTP，不承担对话意图判断与轮次用例编排 |
| `kernel/` | 跨模块共享的领域概念与纯规则 | 可引用明确允许的模块 DTO，不引用服务、不执行 I/O |
| `shared/` | 零业务依赖的原语与框架基类 | 不 import 本工程任何其他分组 |
| `infra/` | 适配器：DB 引擎、Redis、LLM、宿主网关 | 只被组合根构造 |

**`application/` 与 `modules/` 的分界线**（本设计最常被问的一条）：

- 只做领域内的事、结果与该轮对话无关 → `modules/`。例：`cart` 的写车、`catalog` 的上架校验。
- 需要按对话状态选择动作、组合租约检查与业务操作、把多个模块拼成一个答复 → `application/`。例：`CartActionService` 的"提议动作 + 用户确认"。

`sessions` 是例外语义而非依赖例外：它负责会话、轮次租约与回放的持久化原子操作，但不解析购物意图、不生成回复。`cart` 持有待确认动作的存储与确定性校验；何时提出/确认、如何校验来源轮次由 application 协调。今天 `cart/service.py`、`checkout/service.py` 两者混在一起，切开时必须保持同事务的安全门禁（§4.7）。

### 2.2 依赖规则

```
    api ──► application ──► modules ──► shared
      │           │            │  ▲
      └───────────┴────────────┘  └── kernel ──► shared
                  │                   （只读 catalog/pricing/inventory schemas）
                  └──► kernel

    infra ──► modules / application / kernel / shared     （仅被组合根构造）
```

三层含义：

1. **主链单向向下**：`api → application → modules → shared`。
2. **`modules ↔ kernel` 只是包分组级互指，不允许具体文件的导入环**：cart DTO 引用 `kernel.quotes`，后者只引用 catalog/pricing/inventory 的 DTO；这些基础 DTO 不得再引用 kernel。无需整体下沉三个模块的 schemas；还可采用最小公共契约或 Protocol 消除分组互指，但本轮保留既有 DTO，避免无收益的重复建模。kernel 的模块入口白名单由 §6.1 校验。
3. **`infra` 挂在侧面**：可向内依赖消费者声明的 Protocol / DTO；除组合根外，代码不得直接 import infra，更不得构造其适配器。`Depends` 本身不是依赖例外，路由应依赖 `api.deps` 暴露的提供器。

| 来源 → 目标 | api | application | modules | kernel | shared | infra |
| --- | --- | --- | --- | --- | --- | --- |
| **api** | — | ✅ | ✅ 仅公共 `service`/`schemas`/`ports`/`types` | ✅ | ✅ | ❌；仅 `api.deps` 为组合根例外 |
| **application** | ❌ | — | ✅ | ✅ | ✅ | ❌ 经 Protocol |
| **modules** | ❌ | ❌ | ⚠️ 仅跨模块公共入口，不得泄漏 ORM | ✅ | ✅ | ❌ |
| **kernel** | ❌ | ❌ | ⚠️ 仅 catalog/pricing/inventory 的 `schemas` | — | ✅ | ❌ |
| **shared** | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| **infra** | ❌ | ⚠️ 仅为实现 Protocol | ⚠️ 仅为实现 Protocol | ✅ | ✅ | — |

五条硬规则：

1. **`modules` 永不 import `application` 或 `api`**。当前 `cart → commerce_orchestrator`、`shopping_session → models.commerce` 均违反。
2. **跨模块及上层调用只用公共入口**：`service.py` 的公开方法、`schemas.py`、声明为公开的 `ports.py` / `types.py`。禁止导入其他模块的模型、seed、tokens 等内部实现，也禁止通过返回 ORM 绕过边界。当前 pricing/inventory → catalog.models、cart → shopping_session.models、checkout → cart.models 均需修正（§4.7）。组合根、迁移模型注册与模块自身内部调用是明确例外。
3. **`shared` 不依赖本工程任何模块**。它可依赖 pydantic、SQLAlchemy 核心等第三方基础库，但 `Base` 这类框架原语之外不得含业务语义。
4. **应用包内只有组合根（`ecom/main.py`、`ecom/api/deps.py`）可以构造 `infra`**。其他层只接收注入的实现；仓库级 tools/migrations 的独立进程入口不属于 HTTP 应用包，可在入口装配所需资源，但必须有独立的 CLI/迁移验证。
5. **`application` 永不 import `api`**。用例输入输出放在 application；HTTP 适配层可复用或转换它们，不能让图节点反向依赖 HTTP 契约。

### 2.3 为什么需要独立的 `kernel/`

`SKUQuote`（SKU 快照 = 目录事实 + 现价 + 可售量 + 判定结果）与"可购买性判定"跨越 catalog、pricing、inventory 三个模块，被 cart、`application.shopping`、`application.storefront`、`api` 共同使用。它必须有一个不属于任何单一模块的家：

- 放 `catalog` 不行 —— `catalog` 目前刻意用 `PriceReader` Protocol 避免 import `pricing`（`catalog/service.py:79-86`），把跨模块组合塞进去会让这个已有的良好设计退化。
- 放 `shared` 不行 —— `shared` 不许依赖 `modules`。
- 放 `application` 不行 —— `modules/cart` 需要它。

因此新增 `kernel/`：放 `SKUQuote`、购物约束与纯判定函数，明确只允许引用 catalog/pricing/inventory 的 schemas，不含数据库/网络查询。带 I/O 的 `quote_many` 与 `lock_quotes` 放入 `modules/sale_facts/service.py`，供 cart、应用用例复用（§4.4）。`sale_facts` 是跨模块业务能力门面，不宣称是新的独立限界上下文，不新增表。

比较过三种事实读取归属：放 application 最少新增文件，但 cart 会反向依赖；在 kernel 引入读取 Protocol 可行，但让共享词汇层承担 I/O 与异常适配；本方案选择 modules 中的只读能力门面，允许通过其他模块公共 service 组合事实，kernel 保持纯规则。代价是一个小模块，收益是异常归一与锁定顺序有唯一归属；仍依赖三个模块共享同一数据库，未来接外部服务需重新设计事务协议。

## 3. 目标目录结构

```
ecom/                             ← 唯一包根（沿用 ECOM_ 配置前缀）
├── main.py                       ← 组合根：create_app() + lifespan + 路由注册
├── config.py                     ← 原 config/settings.py
│
├── shared/                       ← 零业务依赖内核
│   ├── errors.py                 DomainError（无业务码的模块异常基类）
│   ├── ids.py                    new_id
│   ├── time.py                   utcnow、UtcDateTime、mark_utc
│   ├── principal.py              Principal、make_principal、principal_key_for
│   ├── hashing.py                request_hash（规范化 JSON 哈希）
│   └── db.py                     Base、TimestampMixin、DATETIME6、NAMING_CONVENTION
│
├── kernel/                       ← 跨模块领域概念与规则
│   ├── errors.py                 CommerceError、CommerceDependencyError（不含 HTTP 状态码）
│   ├── quotes.py                 SKUQuote、CandidatePage、evaluate_purchasability（无 I/O）
│   ├── intent.py                 ShoppingIntent、IntentUpdate、HardCondition
│   └── constraints.py            matches_product_constraints、matches_budget
│
├── infra/                        ← 适配器
│   ├── db.py                     engine、create_session_factory
│   ├── redis.py                  Redis 客户端
│   ├── llm.py                    LLMCommerceModel（ChatOpenAI 适配）
│   └── checkout_gateway.py       宿主结账网关（Demo / Unavailable）
│
├── modules/                      ← 业务模块（内部保持 models/schemas/service 三段式）
│   ├── catalog/     {models, schemas, service, seed, ports}   ← PriceReader、SKU 存在性读取端口
│   ├── pricing/     {models, schemas, service}
│   ├── inventory/   {models, schemas, service, seed}
│   ├── sale_facts/  {service}                                ← 三个事实服务的显式门面，无表
│   ├── cart/        {models, schemas, service}                ← 注入 SaleFactsReader；不持 TurnClaim
│   ├── checkout/    {models, schemas, service, ports}         ← HostCheckoutGateway 留 ports
│   ├── identity/    {models, service, types, tokens, passwords, csrf, rate_limit}
│   ├── sessions/    {models, schemas, service}
│   └── events/      {models, schemas, service}
│
├── application/                  ← 用例编排
│   ├── shopping/
│   │   ├── orchestrator.py       ← 原 commerce_orchestrator/service.py
│   │   ├── graph.py  nodes.py
│   │   ├── intent.py  discovery.py  compare.py
│   │   ├── contracts.py          TurnRequest、TurnResponse（用例 DTO，可被 API 复用）
│   │   ├── actions.py            CartActionService：购物车/结账动作与轮次门禁编排
│   │   ├── facts.py              CommerceFactsReader：目录扫描 + SaleFactsReader 委托
│   │   ├── guards.py             SafeIntentModel、SafeCandidateReranker
│   │   ├── ports.py              IntentModel、CandidateReranker Protocol
│   │   └── state.py              CommerceGraphState
│   └── storefront/               ← 原 storefront/
│
└── api/                          ← 接口层
    ├── deps.py                   ← 原 api/runtime.py：Runtime 容器 + require_* 依赖
    ├── errors.py                 ← 统一异常 → HTTP 映射
    ├── middleware.py             ← CORS、校验错误处理器、client_ip 解析
    ├── http_config.py            IdentityHttpConfig、可信代理配置（不留在 identity）
    ├── contracts/
    │   ├── commerce.py           HTTP 请求封装；导入用例 TurnRequest/TurnResponse
    │   └── auth.py               注册/登录/刷新等请求响应模型
    └── routes/
        ├── health.py  metrics.py
        ├── auth.py               ← 原 api/auth.py + api/identity.py 的路由部分
        ├── catalog.py            ← 原 api/catalog.py + api/commerce_read.py（合并）
        ├── storefront.py
        ├── commerce.py           ← 原 api/commerce.py
        └── demo.py               ← 原 api/commerce_demo.py

# 仓库级（非代码）
frontend/     前端（独立构建产物，保持顶层）
migrations/   Alembic（保持顶层，Alembic 约定）
tests/        与被测分组同构：tests/unit/、tests/integration/
tools/        原 data/ + scripts/ 合并
docs/
```

`demo/` 与 `frontend/` 保持顶层（见 §9 决策 D3）。图中省略各包的 `__init__.py`。旧推荐栈默认保留；D1 未确认而已执行包根搬迁时，它放在 `ecom/legacy/`，现有路由仍注册。legacy 可以使用新模块公共入口，但 modules/application/kernel/shared/infra 不得依赖 legacy；仅组合根及指定的旧接口路由可引用它。保留的实验/指标暂随 legacy 放置，不新增未定义依赖方向的 `experiments/` 第七业务分组（§5.5）。

## 4. 层间契约

### 4.1 异常归属与兼容映射

`shared/errors.py` 只声明不含业务语义的 `DomainError(Exception)`。`CatalogError`、`PricingError`、`InventoryError`、`IdentityError` 保持各自模块的异常层级，只修改共同基类。购物流程共享异常迁到 `kernel/errors.py`：

```python
from ecom.shared.errors import DomainError


class CommerceError(DomainError):
    def __init__(self, code: str, details: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


class CommerceDependencyError(DomainError):
    """商品或交易事实源暂不可读取；与查无数据不同。"""
```

内层异常不携带 HTTP 状态码。当前 `CommerceError(code, status_code, details)` 的调用需逐项迁为语义错误码/类型；application 中对 `exc.status_code == 404` 的判断也改为业务错误码判断，不能遗留 HTTP 语义。`api/errors.py` 集中维护映射，不把所有 `DomainError` 一律处理成同一个错误体：

| 现有场景 | 迁移后兼容要求 |
| --- | --- |
| catalog 的 not found / conflict / validation | 分别保持 404/409/422，`detail` 仍为原字符串 |
| commerce 的 `CommerceError` | 根据现有错误码登记 404/409/422/503 等，保留 `detail={code, ...details}` |
| commerce/storefront 的依赖故障 | 保持 503 与 `detail={code: dependency_unavailable}` |
| 单 SKU 报价的价格/库存故障 | 保留现有 503 与各自的字符串错误体，不因合并路由而替换 |
| identity 的认证、CSRF、限流错误 | 保留原状态码、错误体、`Retry-After`、Cookie 清理等响应副作用 |

未登记的异常作为内部错误处理，不将数据库错误、凭证或配置秘密写入响应。结构提交中的错误响应测试期望不变；统一外部错误形状必须另立兼容性变更，不能作为本分层方案的默认动作。

### 4.2 `kernel/quotes.py`（规则单一归属）

当前三处拷贝的合并目标，**取最完整的一份**（`commerce_orchestrator/facts.py:31-44`）：

```python
def evaluate_purchasability(
    sku: SKUDetailView | None,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> str:
    """可购买性判定的唯一实现。

    返回 reason 码；只有 'purchasable' 表示可买。
    顺序即优先级：SKU 状态 → 商品状态 → 价格（含币种）→ 库存状态 → 可售量。
    """
    if sku is None:
        return 'sku_not_found'
    if sku.status != 'active':
        return 'sku_inactive'
    if sku.product_status != 'active':
        return 'product_not_active'
    if price is None or price.currency != 'CNY':
        return 'price_missing'
    if availability is None or availability.status == 'unknown':
        return 'stock_unknown'
    if (availability.status != 'available'
            or availability.available_quantity is None
            or availability.available_quantity < 1):
        return 'out_of_stock'
    return 'purchasable'


class SKUQuote(BaseModel):
    sku_id: str
    sku: SKUDetailView | None = None
    price: PriceView | None = None
    availability: AvailabilityView | None = None
    purchasable: bool
    reason: str
    read_at: UtcDateTime


class CandidatePage(BaseModel):
    items: list[SKUQuote] = Field(default_factory=list)
    incomplete: bool = False
    scanned: int = 0
```

调用方改为：

- `api/routes/catalog.py`（原 `commerce_read.py`）→ `evaluate_purchasability(sku, price, availability)`
- `modules/sale_facts/service.py` 的 `quote_many` / `lock_quotes` → 取得 DTO 后调用同一函数；锁定路径传 `SaleFactView.sku`
- `modules/cart/service.py` 与 `application/shopping/facts.py` → 委托 `SaleFactsReader`，不再保留判定副本

结构提交先抽取各路径已有逻辑并保持现有输出；随后单独提交规则归一，明确修正原报价 API 缺少币种检查、原购物车锁定路径缺少库存状态检查的差异。所有路径均采用上面的负库存防御与优先级；不额外改 `AvailabilityView` 的验证约束，以免结构迁移时改变已有输入容忍度。

验收不止覆盖七种 reason：还要覆盖负库存、`None` 数量、非 CNY、SKU/商品状态冲突、多个失败条件同时存在的优先级，以及普通读取/同事务锁定读取的规则一致性。缺失 SKU 在单 SKU HTTP 端点仍返回 404，不能因统一判定变成 200 报价占位。

### 4.3 `kernel/constraints.py`

`commerce_orchestrator/facts.py:63-75` 与 `cart/service.py:166-172` 的条件逻辑合并。商品条件与价格预算是两个输入不同的纯函数，不再使用无法获取现价的 `matches_hard_conditions(intent, product)` 承诺校验预算：

```python
def matches_product_constraints(
    intent: ShoppingIntent,
    product: ProductView,
    category_attributes: Sequence[AttributeView],
) -> bool:
    """类目与 hard_conditions 是否满足；不判断价格或库存。

    推断来源（source == 'inferred'）的条件不参与过滤；类型不符视为不匹配。
    """


def matches_budget(intent: ShoppingIntent, price: PriceView | None) -> bool:
    """要求现价存在、币种符合 intent.currency，且金额不超过已声明预算。"""
```

扫描路径传入 `ProductDetailView` 及其 `category_attributes`；锁定路径传入 `SaleFactView.product` / `category_attributes`，预算两处均传现行 `PriceView`。两种视图都由 catalog 提供，不读取 ORM。保持金额为整数分、上限包含等号、推断硬条件不参与过滤、不可过滤/缺失属性及严格类型不匹配即失败的现有语义。新增类目不符、预算等值/超额、未知条件、`bool` 与 `int` 类型差异的回归测试。

### 4.4 事实读取与 `modules/cart` 的依赖倒置

**改造前**（`cart/service.py:8,16,17`，模块反向依赖应用层）：

```python
from commerce_orchestrator.facts import CommerceFactsReader, CommerceDependencyError
from commerce_orchestrator.schemas import SKUQuote, ShoppingIntent
from commerce_orchestrator.utils import request_hash
```

**改造后**：新增 `modules/sale_facts/service.py` 的 `SaleFactsReader`，集中持有明确的三个事实服务；cart 只消费其公开事实能力，不再访问 `self.facts.catalog` 等内部属性。

```python
from ecom.modules.sale_facts.service import SaleFactsReader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

class CartService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], facts: SaleFactsReader,
                 max_quantity: int = 10, action_ttl_seconds: int = 600) -> None:
        ...
```

`SaleFactsReader(catalog, pricing, inventory)` 提供两种公共方法：

| 方法 | 契约 |
| --- | --- |
| `quote_many(sku_ids)` | 去重、批量只读、为请求的每个 ID 返回 `SKUQuote`；空输入不做 I/O；缺数据与依赖故障分开 |
| `lock_quotes(db, sku_ids)` | 在调用方事务中按既有锁顺序读取；返回报价及 `SaleFactView` DTO；不提交/回滚，不调用网络 |

事实读取门面可 import catalog/pricing/inventory 的公共 service 及其异常，捕获现有明确的 `SQLAlchemyError`、`CatalogError`、`PricingDependencyError`、`InventoryDependencyError` 后转换为 `kernel.errors.CommerceDependencyError`；不使用裸 `except Exception` 隐藏编程错误。`kernel.quotes` 不承担这些调用或转换。保持现有顺序读取，不在重构中顺便改为并行；并行带来的连接数与一致性取舍另行评估。

`CommerceFactsReader` 留在 `application/shopping/facts.py`，只持 catalog 与 `SaleFactsReader`，保留 `scan` 并委托报价读取。`ShoppingIntent` / `IntentUpdate` / `HardCondition` 是购物规则的共享词汇，迁往 `kernel/intent.py`，不因为持久化在 `state_json` 就归 sessions 所有。`request_hash` 迁往 `shared/hashing.py`。cart 不再持会话服务或 `TurnClaim`，会话门禁通过 application 的动作用例组合（§4.7）。

相比给每个调用方分别注入三个服务，能力门面减少重复装配与异常转换；它不是应用层服务定位器，因为只暴露 `quote_many` / `lock_quotes` 这一项业务能力，禁止调用方穿透访问内部服务。代价是增加一个无表的业务能力模块；三项真实依赖在门面构造中仍明确可见。

### 4.5 `modules/sessions` 与响应契约解耦

**改造前**（`shopping_session/service.py:10,94`，模块依赖 API 契约）：

```python
from models.commerce import TurnResponse
...
if (completion.response.session_id != claim.session_id
        or completion.response.turn_id != claim.turn_id):
    raise CommerceError('invalid_completion', 422)
```

**改造后**：sessions 的完成与回放都携带不透明 JSON 及归属字段；完整形状在 application 校验。

```python
class TurnCompletion(BaseModel):
    session_id: str
    turn_id: str
    response: dict
    state: dict = Field(default_factory=dict)
    product_ids: list[str] = Field(default_factory=list)
    sku_ids: list[str] = Field(default_factory=list)


class TurnReplay(BaseModel):
    session_id: str
    turn_id: str
    response: dict
```

`TurnRequest` / `TurnResponse` 迁往 `application/shopping/contracts.py`，保留现有字段、验证限制与序列化输出；orchestrator、图节点、图 state 都从此处导入。API 可直接复用这两个不含 FastAPI/HTTP 依赖的用例 DTO；其余仅由路由消费的输入仍在 `api/contracts/commerce.py`。不为同一形状复制两套 Pydantic 模型。

完成/回放流程必须同时迁移：

1. application 完成前使用 `TurnResponse.model_validate` 验证全部响应，并核对 `session_id` / `turn_id` 与 claim 一致，再以 `model_dump(mode='json')` 生成保存内容和 `TurnCompletion`。
2. sessions 检查 completion 的归属字段与 claim 一致，同时检查 payload 中这两个字段与归属字段一致、内容可序列化为 JSON；失败仍为 `invalid_completion`。这不是完整响应校验，但防止新增外层字段与实际存储内容脱节。
3. sessions 在既有事务内校验租约、保存 response/state/seen 集合、增加版本并释放租约。保持 `response_json` 原形状，不新增数据库列或包裹层。
4. claim 的回放分支直接取已存 JSON，构造 `TurnReplay`；application 再验证 `TurnResponse` 和归属字段后返回。不能遗留 sessions 中的 `TurnResponse.model_validate` 导入，也不能让 API 成为落库前唯一校验点。

代价是 sessions 不再验证全部响应字段，责任移到 application；用非法响应拒绝落库、归属不匹配、过期租约、同轮次原样回放测试证明该责任未丢失。

### 4.6 其余归属

| 类型 | 现位置 | 目标 |
| --- | --- | --- |
| `Principal` 及构造 | `identity/types.py` | `shared/principal.py` |
| `new_id`、`utcnow`、`UtcDateTime` | `db/base.py` | `shared/ids.py`、`shared/time.py` |
| `Base`、`TimestampMixin` | `db/base.py` | `shared/db.py` |
| `CommerceError`、`CommerceDependencyError` | `shopping_session/service.py`、`commerce_orchestrator/facts.py` | `kernel/errors.py` |
| `TurnRequest` / `TurnResponse` | `models/commerce.py` | `application/shopping/contracts.py`，API 复用 |
| `CreateSessionRequest`/`CompareRequest`/`ConfirmationRequest`/`CheckoutPreviewRequest`/`CheckoutHandoffRequest`/`ProductContextRequest`/`EventRequest` | `models/commerce.py` | `api/contracts/commerce.py` |
| `HostCheckoutGateway` Protocol、`validate_destination` | `checkout/gateway.py` | `modules/checkout/ports.py`、`modules/checkout/service.py`；网关实现移 `infra/checkout_gateway.py` |
| `IntentModel`/`CandidateReranker` Protocol | `commerce_orchestrator/model.py` | `application/shopping/ports.py` |
| `LLMCommerceModel` | `commerce_orchestrator/model.py` | `infra/llm.py` |
| `DEMO_ENVIRONMENTS` | `identity/types.py` | `ecom/config.py`（部署模式常量） |
| `IdentityHttpConfig`、`parse_trusted_networks` | `identity/types.py` | `api/http_config.py`；identity 仅接收业务所需的配置值 |

`shared/db.py` 保留 SQLAlchemy 声明式基类是刻意的：`Base` 是**框架原语**而非适配器，各模块的 `models.py` 需要它而 `modules → infra` 是被禁止的。真正的适配器是 engine 与 session factory，它们在 `infra/db.py`。

### 4.7 跨模块公共操作与事务所有权

不能只删除 import：当前 pricing/inventory 校验 catalog 的 SKU、cart 查询 sessions 的 SeenSKU、checkout 读写 cart 的动作/车行都位于安全关键路径。目标映射如下；方法名为拟新增公共接口，签名与 DTO 在实施前通过对应测试固定。

| 当前越界 | 公共替代能力 | 事务约定 |
| --- | --- | --- |
| pricing / inventory 的 `_require_sku` 直接 import `catalog.models.SKU` | catalog 公开 `require_sku_exists(db, sku_id)`，返回无 ORM 的结果；调用方转换为本模块的 not found 异常 | 使用价格/库存调用方现有事务，保留数据库 FK 与冲突处理 |
| cart 直接查询 `shopping_session.models.SeenSKU` | sessions 公开 `has_seen_sku(db, session_id, sku_id)`；application 动作用例负责所有权、轮次与已展示校验 | 与动作落库共用一个事务，不能先开另一连接检查再写 |
| checkout 直接查询 `cart.models.CartLine` / `PendingAction` | cart 公开 `lock_cart_snapshot`、`lock_pending_action`、`find_action_owner` 及受控的动作创建/消费/回执操作 | 返回 `CartSnapshot` / `PendingActionView` 等 DTO；查询和更新各自的表始终由 cart 执行 |
| `owned_row` / `ensure_cart` 等跨模块返回 ORM | sessions/cart 公开会话状态、车版本 DTO 与原子操作；原 ORM helper 仅模块内部使用 | 上层只传 ID、版本、命令和 DTO，禁止经服务返回值改写其他模块表 |
| cart/checkout 内的 `source_turn`、租约与会话状态编排 | `application/shopping/actions.py` 的 `CartActionService` | 在同一个事务中组合 sessions 与 cart/checkout 的无提交方法 |

新增 catalog 存在性能力及其消费端口在 catalog 公开 `ports.py` 声明；组合根注入 catalog 实现给 pricing/inventory，避免两者重新直接读目录表。保留现有未锁定的存在性查询语义，不擅自增加锁；价格/库存事实的锁定读取沿用既有公共方法。

**事务所有者**：单模块独立 CRUD 仍可自管事务；购物车提案/确认、结账预览/确认准备等多模块用例由 `CartActionService` 使用注入的 session factory 建立事务。被调用的公开 `*_in_transaction` 操作接收同一个 `AsyncSession`，不得再开 session、commit 或 rollback；异常由外层上下文统一回滚。无须现在引入通用 Unit of Work/repository 框架，代价是应用用例感知 SQLAlchemy，但符合现有技术约束。

**安全时序**：保留“锁会话并核对主体/过期/轮次租约 → 锁购物车及相关动作 → 读取当前车版本与已展示集合 → 按目录 → CNY 价格 → 库存的既有顺序锁事实 → 校验条件/数量/快照 → 原子创建/确认动作、写车与回执”的临界区。具体路径沿用现有是否加锁、排序键与重放次序，以源代码和并发测试为基线；不能为了图示统一时序而改变原来的幂等回放优先级。同一轮次已过期时不得在事务外先检查后执行写入，动作检查/落库之间也不得释放锁。

`source_turn_id` 可作为不透明的幂等关联值随动作命令传给 cart，模块不接收 `TurnClaim`、不解释轮次租约。无 source_turn 的现有直接 API 操作也经同一 application 用例，保留会话归属、动作有效期、购物车版本与确认校验。

结账保持两段本地事务与锁外宿主调用：第一段在同事务中校验门禁、保存 preparing 并消费动作；提交后调用网关，使用同一 handoff ID 作宿主幂等键；第二段锁定 handoff 并保存 ready/failed。不能将网关调用塞进长事务，也不能把 preparing 与动作消费拆成独立提交。完整轮次保存仍按现有流程另行短事务完成，本次不增加跨网络长事务。

必须在真实 MySQL 下覆盖：并发提案/确认、租约被接管、确认重复/冲突、价格或库存改变后重新确认、事务中间异常后的全量回滚，以及 checkout preparing/失败重试不重复交接；DTO 单测不能替代这些验证。

### 4.8 组合根装配：消除构造后回填（P5）

`api/deps.py` 不只是 `runtime.py` 改名。装配顺序固定为：

1. 构造 DB/Redis 资源及网关、LLM 等可用适配器；保留现有未配置时的不可用状态，初始化失败清理已创建资源。
2. 构造 pricing/inventory/catalog；catalog 的存在性读取端口通过构造参数注入 pricing/inventory。catalog 对价格的既有 `PriceReader` 与新增存在性端口若造成构造环，先构造仅依赖 factory 的 `CatalogSKUReader` 公共实现，再构造 pricing → catalog，不采用回填。
3. 构造 `SaleFactsReader(catalog, pricing, inventory)`、sessions、cart、checkout、events，再构造 `CartActionService(factory, sessions, cart, checkout)`。
4. 构造安全模型包装、discovery/comparison、`CommerceGraphDependencies` 与图；构造 `CommerceOrchestrator(sessions, graph, actions, comparison)`。图节点通过同一个 actions 实例编排写入，不再直接调用模块中的 source_turn 方法；图、动作用例与模型依赖均在构造时给定，不允许构造后赋值或请求内懒装配。
5. 返回完整 Runtime 容器，由 `create_app()` / lifespan 挂到 `app.state`；路由只取依赖提供器。测试通过相同构造入口传入本地桩。

Runtime 可以有明确的不可用组件和原因，不能暴露“构造成功但购物车/图尚未回填”的中间态。生命周期所有者负责关闭 Redis 与 DB engine；移包时不得遗漏原关闭行为。验证实例唯一性、图节点使用同一 cart/checkout/模型实例，以及配置缺失/适配器故障下的既有 503 行为。保留旧栈时其现有实例分裂问题另立行为修复，不混入新购物栈的机械搬迁。

## 5. 全量文件映射

### 5.1 业务模块

| 现状 | 目标 |
| --- | --- |
| `catalog/{__init__,models,schemas,service,seed}.py` | `ecom/modules/catalog/` |
| `catalog/legacy_adapter.py` | 默认保留；包根搬迁时移 `ecom/legacy/catalog_adapter.py`，D1 确认后才删除 |
| `pricing/{__init__,models,schemas,service}.py` | `ecom/modules/pricing/` |
| `inventory/{__init__,models,schemas,service,seed}.py` | `ecom/modules/inventory/` |
| `cart/{__init__,models,schemas,service}.py` | `ecom/modules/cart/` |
| `checkout/{__init__,models,schemas,service}.py` | `ecom/modules/checkout/`；抽出 source_turn / sessions 编排到 application 动作用例 |
| `checkout/gateway.py` | 拆分：Protocol → `modules/checkout/ports.py`；目的地校验 → `modules/checkout/service.py`；实现 → `infra/checkout_gateway.py` |
| `identity/{__init__,models,service,tokens,passwords,csrf,rate_limit}.py` | `ecom/modules/identity/` |
| `identity/types.py` | 拆分：`Principal` 系列 → `shared/principal.py`；HTTP 配置 → `api/http_config.py`；部署常量 → config；用户视图/身份错误留 `modules/identity/types.py` |
| `shopping_session/{__init__,models,schemas,service}.py` | `ecom/modules/sessions/` |
| `commerce_events/{__init__,models,schemas,service}.py` | `ecom/modules/events/` |
| 新增 `SaleFactsReader` | `ecom/modules/sale_facts/service.py`；承接 quote_many / lock_quotes 与明确的依赖错误转换 |
| 新增 catalog 存在性读取能力 | `ecom/modules/catalog/ports.py` 的消费契约与 `service.py` 的 `CatalogSKUReader`；只依赖 factory 的实现避免构造环 |

### 5.2 编排与应用

| 现状 | 目标 |
| --- | --- |
| `commerce_orchestrator/service.py` | `ecom/application/shopping/orchestrator.py` |
| `commerce_orchestrator/graph.py` | `ecom/application/shopping/graph.py` |
| `commerce_orchestrator/nodes.py` | `ecom/application/shopping/nodes.py` |
| `commerce_orchestrator/intent.py` | `ecom/application/shopping/intent.py` |
| `commerce_orchestrator/discovery.py` | `ecom/application/shopping/discovery.py` |
| `commerce_orchestrator/compare.py` | `ecom/application/shopping/compare.py` |
| `commerce_orchestrator/facts.py` | 拆分：scan → `application/shopping/facts.py`；quote_many → `modules/sale_facts/service.py`；错误 → `kernel/errors.py`；纯条件匹配 → `kernel/constraints.py` |
| `commerce_orchestrator/model.py` | 拆三处：Protocol → `application/shopping/ports.py`；`SafeIntentModel`/`SafeCandidateReranker` → `application/shopping/guards.py`；`LLMCommerceModel` → `infra/llm.py` |
| `commerce_orchestrator/schemas.py` | 拆分：`SKUQuote`/`CandidatePage` → `kernel/quotes.py`；`ShoppingIntent`/`IntentUpdate`/`HardCondition` → `kernel/intent.py`；`CommerceGraphState` → `application/shopping/state.py` |
| `commerce_orchestrator/utils.py` | `shared/hashing.py` |
| `storefront/{__init__,schemas,service}.py` | `ecom/application/storefront/` |
| `models/commerce.py` 的 `TurnRequest` / `TurnResponse` | `ecom/application/shopping/contracts.py`；orchestrator/nodes/state/API 均使用此归属 |
| cart/checkout 中的会话、轮次与来源门禁 | `ecom/application/shopping/actions.py`；模块中的数据校验/写入按 §4.7 保留为无提交公共操作 |

### 5.3 接口层

| 现状 | 目标 |
| --- | --- |
| `api/runtime.py` | `ecom/api/deps.py` + `ecom/infra/{db,redis}.py`（引擎与客户端构造抽出） |
| `api/catalog.py`、`api/commerce_read.py` | 合并为 `ecom/api/routes/catalog.py` |
| `api/commerce.py` | `ecom/api/routes/commerce.py`；购物车/结账写入改调用 application 动作用例，保留 URL 与响应；文件中途 import 归位到文件头 |
| `api/storefront.py` | `ecom/api/routes/storefront.py` |
| `api/auth.py` | `ecom/api/routes/auth.py`（BaseModel 部分 → `api/contracts/auth.py`） |
| `api/identity.py` | 路由相关留 `routes/auth.py`；CORS/校验错误处理/client_ip → `ecom/api/middleware.py` |
| `api/commerce_demo.py` | `ecom/api/routes/demo.py` |
| `api/health.py`、`api/metrics.py` | `ecom/api/routes/` 同名 |
| `models/commerce.py` | 拆入 `application/shopping/contracts.py` 与 `api/contracts/commerce.py`；events 的已有 DTO 保持模块归属；所有调用方更新后删除原文件，不属于旧栈退役 |
| 新增 | `ecom/api/errors.py`（合并 `api/catalog.py:22-28` 的 `catalog_http_error`、`api/commerce.py:20-26` 的 `invoke`、`api/storefront.py:17-21` 的 `read`） |

### 5.4 基础设施与共享

| 现状 | 目标 |
| --- | --- |
| `db/base.py` | 拆入 `shared/{db,ids,time}.py` |
| `db/session.py` | `ecom/infra/db.py` |
| `config/settings.py` | `ecom/config.py` |
| `main.py` | `ecom/main.py` |
| `infrastructure/{__init__,mysql_client,redis_client,milvus_client}.py` | 默认保留；包根搬迁时移 `ecom/legacy/infrastructure/`，独立清理提交中删除（当前仅自身测试引用） |

### 5.5 条件退役清单与保留分支

以下是待批准的清理候选，不是默认删除清单。D1 未确认时保留现有路由、响应和旧栈测试；包根搬迁仍可执行，按保留位置迁移旧代码并更新 import。

| 路径 | 包根搬迁时的保留位置 | 条件退役理由 |
| --- | --- | --- |
| `agents/` | `ecom/legacy/agents/` | 旧推荐样例；需按具体 Agent 核对真实依赖，不能笼统称全部依赖 MOCK_PRODUCTS |
| `orchestrator/` | `ecom/legacy/orchestrator/` | 旧推荐管线与实例分裂问题，需独立决定退役或修复 |
| `models/schemas.py`、`models/__init__.py` | `ecom/legacy/models/` | 浮点金额的旧展示契约 |
| `services/feature_store.py` | `ecom/legacy/services/` | 尚未接入当前购物栈；仅在独立清理中删除 |
| `infrastructure/` | `ecom/legacy/infrastructure/` | 当前无生产调用方；须同步处置自身测试 |
| `catalog/legacy_adapter.py` | `ecom/legacy/catalog_adapter.py` | 旧推荐栈的过渡适配器 |
| `api/recommendations.py`、`api/experiments.py` | `ecom/api/routes/legacy_recommendations.py`、`legacy_experiments.py` | 公共接口，只在 D1 明确确认退役且说明调用方影响后移除 |

`services/ab_test.py`、`services/metrics.py` 默认迁入 `ecom/legacy/services/`；`api/routes/metrics.py` 保持当前输出，其 legacy 引用仅作为过渡例外。若 D1 同意退役，D2 必须同时决定实验/指标的消费者：仅删除推荐调用链不等于自动删除 metrics/experiments。需要新的购物指标能力时另行明确归属，不凭空新增 `ecom/experiments/`。本文不要求创建 Git 分支或提交旧代码。

退役提交附被删除路由清单、调用方核查、文档/API 示例变化和对应测试退出清单；不得先删测试以获得绿灯。新业务层依赖 legacy 的禁止规则始终有效，组合根与三个过渡路由的直接引用例外单独登记，并在消费者退役时删除。

### 5.6 仓库级目录

| 现状 | 目标 |
| --- | --- |
| `data/{download,import}_amazon_metadata.py`、README、requirements | `tools/amazon/`；更新离线依赖与 CLI 使用说明，不与 Python 应用依赖混为一体 |
| `scripts/reset_demo_password.py` | `tools/reset_demo_password.py` |
| `tests/` | `tests/unit/…` + `tests/integration/…`；按是否依赖真实服务分类，不仅按文件后缀，保留 JS 合同测试入口 |
| `migrations/`、`frontend/`、`demo/`、`docs/` | 保持顶层 |
| 新增 | `pyproject.toml`（见 §6.1）；包根搬迁前仅启用阶段性工具配置 |

## 6. 落地保障

### 6.1 `pyproject.toml`

当前仓库没有 `pyproject.toml`；统一增加工具配置。以下是**包根迁移完成后的目标配置**，不是第 0 步立即启用的配置；迁移期配置见 §8.1。开发依赖单列并固定实施时验证过的 pytest/ruff/import-linter 版本，不加入生产 requirements。本修订用 import-linter 2.15 验证目标配置的基本正/负例；这不代表尚未创建的目标业务代码已通过架构检查。当前 Docker 使用 Python 3.12，因此目标版本选 3.12；开发机和 CI 也需明确这一前提。

```toml
[project]
name = "ecom"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I"]
ignore = []

[tool.pytest.ini_options]
testpaths = ["tests"]
# 现有测试用 asyncio.run() 显式驱动（见 tests/conftest.py），未使用 pytest-asyncio，
# 因此不设 asyncio_mode。若后续引入该插件再补。

[tool.importlinter]
root_packages = ["ecom"]

[[tool.importlinter.contracts]]
id = "layers"
name = "分层依赖方向"
type = "layers"
layers = [
    "ecom.api",
    "ecom.application",
    "ecom.modules",
    "ecom.shared",
]

[[tool.importlinter.contracts]]
id = "modules-boundary"
name = "业务模块不得依赖应用层与接口层"
type = "forbidden"
source_modules = ["ecom.modules"]
forbidden_modules = ["ecom.api", "ecom.application", "ecom.infra"]

[[tool.importlinter.contracts]]
id = "kernel-boundary"
name = "kernel 不得依赖业务服务与模型"
type = "forbidden"
source_modules = ["ecom.kernel"]
forbidden_modules = [
    "ecom.modules.catalog.service", "ecom.modules.catalog.models",
    "ecom.modules.pricing.service", "ecom.modules.pricing.models",
    "ecom.modules.inventory.service", "ecom.modules.inventory.models",
    "ecom.modules.cart.service", "ecom.modules.cart.models",
    "ecom.modules.checkout.service", "ecom.modules.checkout.models",
    "ecom.modules.identity.service", "ecom.modules.identity.models",
    "ecom.modules.sessions.service", "ecom.modules.sessions.models",
    "ecom.modules.events.service", "ecom.modules.events.models",
    "ecom.modules.sale_facts.service",
]

[[tool.importlinter.contracts]]
id = "kernel-upper-boundary"
name = "kernel 不得依赖上层"
type = "forbidden"
source_modules = ["ecom.kernel"]
forbidden_modules = ["ecom.api", "ecom.application", "ecom.infra"]

[[tool.importlinter.contracts]]
id = "inventory-catalog-models"
name = "inventory 不得引用 catalog 的模型层"
type = "forbidden"
source_modules = ["ecom.modules.inventory"]
forbidden_modules = ["ecom.modules.catalog.models"]
allow_indirect_imports = true

[[tool.importlinter.contracts]]
id = "shared-boundary"
name = "shared 保持零业务依赖"
type = "forbidden"
source_modules = ["ecom.shared"]
forbidden_modules = ["ecom.modules", "ecom.kernel", "ecom.application", "ecom.api", "ecom.infra", "ecom.config", "ecom.main"]

[[tool.importlinter.contracts]]
id = "infra-inner-boundary"
name = "只有组合根可以引用基础设施"
type = "forbidden"
source_modules = ["ecom.modules", "ecom.kernel", "ecom.shared", "ecom.application"]
forbidden_modules = ["ecom.infra"]

[[tool.importlinter.contracts]]
id = "infra-importers"
name = "基础设施只允许组合根直接导入"
type = "protected"
protected_modules = ["ecom.infra"]
allowed_importers = ["ecom.main", "ecom.api.deps"]

[[tool.importlinter.contracts]]
id = "infra-api-boundary"
name = "基础设施不得依赖接口层"
type = "forbidden"
source_modules = ["ecom.infra"]
forbidden_modules = ["ecom.api"]
```

配置中只展开一条跨模块模型契约作为样例；实施时必须生成所有现存模块对的同类约束，不能把这一条当成完整边界检查。上层禁止依赖保持默认的间接检查；仅跨模块“禁止直接访问内部实现”配置 `allow_indirect_imports = true`，允许合法的 `inventory → catalog.service → catalog.models`，但拒绝直接访问 models。[Forbidden 官方说明](https://import-linter.readthedocs.io/en/stable/contract_types/forbidden/)

`protected` 限制所有非组合根的直接 infra 导入，包括 api/routes、middleware、contracts；允许 infra 内部互相引用。不能因为路由使用 `Depends` 就直接 import 某个 infra 类。内层另有 forbidden 契约，仍禁止通过组合根间接到达 infra。[Protected 官方说明](https://import-linter.readthedocs.io/en/stable/contract_types/protected/)

仅 import-linter 不足以表达全部白名单，增加 `tests/unit/test_layering.py` 的 AST 边界检查作为必需补充：

- 遍历 application、api、kernel、shared、modules、infra 的所有 `.py`，解析文件头、函数内、`TYPE_CHECKING` 下的 `import` / `from`，将相对引用解析为绝对模块名。
- kernel 访问 modules 的完整直接白名单是 `catalog.schemas`、`pricing.schemas`、`inventory.schemas`；禁止其余入口、I/O 调用和实例化事实服务。基础 schemas 仅依赖 shared/第三方，不能反向 import kernel。
- 跨模块及 application/api/infra 访问业务模块时，只允许 §2.2 的公共入口；组合根可构造模块内部的适配实现，迁移模型注册不纳入应用包检查。service 的公共符号需显式导出，不得重导出 ORM 或通过 `_` 私有符号越界。
- 确认 `ecom.modules` 下的每个模块都进入契约清单；漏掉新模块、目标包/文件不存在或相对路径无法解析均失败。源分组 `__init__.py` 不重导出业务实现，不允许借包级别导入绕过白名单。
- shared 不导入任何本工程包；kernel 不含 SQLAlchemy 会话/Redis/LLM 调用；modules/application 不含 HTTP 状态码、FastAPI 类型或 api 导入。身份的 HTTP 专属配置类型另迁 api 配置边界，身份内部仅持令牌/会话所需配置。
- D1 未确认时另外启用“内层不得依赖 legacy”的 forbidden 契约；直接 legacy 白名单仅为组合根与 §5.5 的三个过渡路由。D1/D2 决定全部移除 legacy 后才删除该契约，不配置不存在的 forbidden 目标。

边界检查必须有正/负例：允许跨模块公共服务调用及拥有者内部 ORM；拒绝直接模型访问、私有符号/ORM 重导出、kernel 服务引用、应用层 API DTO 引用、路由 infra 引用、TYPE_CHECKING/函数内/相对引用绕过，以及新模块遗漏。真实包额外做导入 smoke test 验证没有运行期循环。AST 检查不等于安全沙箱，动态导入/反射不允许用于绕过依赖规则，代码评审需核查。

ruff 首轮只配置 E/F/I；历史告警单列基线，仅阻断新增告警，不在机械搬迁中清理全仓格式。UP/B/ASYNC 后续独立启用，避免一次性扩大范围。

### 6.2 CI 断言

CI 将架构检查设为必需任务；可用公开 CLI 单独运行，也通过 pytest 包装 CLI 检查退出码。不要调用内部 `importlinter.cli.lint_imports(...) is True`：当前实现成功返回整数 `0`，不是布尔值，且内部 API 不是稳定的调用边界。[官方源码](https://github.com/seddonym/import-linter/blob/main/src/importlinter/cli.py)

```python
import subprocess
from pathlib import Path


def test_layering_contracts_hold():
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["lint-imports", "--config", "pyproject.toml", "--no-cache"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

上述路径用于迁移后的 `tests/unit/test_layering.py`；迁移前测试位于 `tests/test_layering.py` 时根目录用 `parents[1]`，并显式指向 §8.1 的阶段配置。CLI 未安装、配置无效、包缺失、契约破坏均使测试失败，不使用 `importorskip` / 文件不存在即跳过的分支。

CI 至少有三个独立门禁：

| 门禁 | 必须记录与校验 |
| --- | --- |
| 无外部服务的单元/API 合同任务 | 使用本地桩；执行架构/AST 检查、路由与响应合同、启动 smoke test；保留 Node 的 demo 合同测试 |
| 独立 MySQL 集成任务 | 启动真实 MySQL、设置独立 `ECOM_TEST_DATABASE_URL`，执行迁移/事务/并发测试；所选用例零跳过 |
| 独立 Redis/身份集成任务 | 启动隔离 Redis；所需用例同时配置独立 MySQL/Redis URL；执行原子限流及身份测试，所选用例零跳过 |

每次保存 `pytest --collect-only` 的 node ID 清单及 JUnit XML 中的通过/失败/跳过结果；按 §8 的路径映射对比。结构阶段必须证明原测试全部仍被收集、执行结果不退化，不能靠相同 skip 数或删除用例通过验收。退役阶段的测试删除只能按已审批清单解释；规则修正阶段只能按已记录差异改变断言。专用集成任务发现环境变量缺失、服务不可用或任何 skip 即失败；普通开发机允许明确跳过，但这不构成集成验收。

单元/integration 拆目录时不机械移动根 `tests/conftest.py`；保留 fixture 作用域，更新 `tests.conftest` 辅助导入和被测模块路径。pytest 分类按真实依赖及标记决定，`*_rate_limit.py` 的纯桩测试不必整文件归 integration。

### 6.3 启动、工具与迁移合同

- 源码部署继续从仓库根启动；目标命令为 `python -m uvicorn ecom.main:create_app --factory --host 0.0.0.0 --port 8000`，开发时可加 `--reload`。Docker CMD、README、工具说明一并更新。`create_app()` 返回已配置路由/lifespan 的应用；不保留运行期 `sys.path.insert`。
- `alembic.ini` 的 `prepend_sys_path` 可保留用于仓库级迁移工具找到包根，不等于应用运行期路径补丁。`migrations/env.py` 更新八个原模块的模型导入，目标 metadata 的表名/FK/索引保持不变；新 sale_facts 不注册模型。验证新库 upgrade、旧库 upgrade、downgrade 及无意外 schema diff。
- 此次选择源码目录 + requirements 的现有部署方式，不宣称仅增加 `[project]` 就完成可安装打包。若要 `pip install .` / wheel，需另补 build-system、包发现、依赖声明和静态资源定位；本次不顺便引入这一交付方式。
- `demo` 保持仓库级交付：统一由组合根配置其绝对位置，路由不能根据新的文件深度猜路径。无数据库启动、`GET /health`、demo 静态资源及配置错误时的既有不可用响应分别验收。
- `data` / `scripts` → `tools` 时更新测试导入和 `python -m tools.amazon.import_amazon_metadata` 等工具入口；若保留旧 CLI 则提供薄兼容入口并记录移除条件，不留下依赖工作目录的隐式导入。

### 6.4 文档同步

- 包根与目录实际搬迁时更新 `AGENTS.md` 的 Project Structure 段落；若存在独立的 `CLAUDE.md` 同步核对，不能预设两文件内容相同。新契约位置与保留的 legacy 状态必须写清，不能提前声称 agents/models 已不存在。
- `docs/architecture.md` 改写为现行购物栈与保留旧栈的并存状态；只有 D1 批准退役后才归档旧接口说明。
- `docs/code-walkthrough.md`、`docs/interview-guide.md`、`docs/project-plan.md` 标注适用版本；README、Docker、Alembic、离线工具文档同步实际阶段，避免文档领先代码。

## 7. 备选方案与取舍

| | A. 只加依赖规则，目录不动 | B. 经典横向四层 | **C. 模块化单体（本方案）** | D. 拆微服务 |
| --- | --- | --- | --- | --- |
| 顶层 | 现状 + import-linter | `domain/ application/ infrastructure/ api/`，每层内再按领域分 | `api/ application/ modules/ kernel/ shared/ infra/` | 每模块一仓库 |
| 改动量 | 极小 | 大 | 中 | 极大 |
| 解决的病症 | 仅目录不动也可处理 P2/P4/P5，P1/P3 仍存在 | 配合契约与规则抽取可解决 P1–P5 | 配合契约与规则抽取可解决 P1–P5 | 目录拆分本身不解决规则/装配问题，可获得独立部署 |
| 主要代价 | 顶层仍分不出层 | 横向组织会增加跨目录阅读；并非必须拆散所有 DTO | 大范围 import 与工具入口更新，增加一个事实读取能力模块 | 运维负担、分布式事务 |
| 适合 | 只想止血 | 领域规则复杂、需多人维护纯领域层 | ✅ 本仓 | 团队与流量到位后 |

**为什么选 C**

- 8 个既有业务模块当前形状统一且内聚（`catalog/service.py` 的规则注释质量很高）。B 也可以保留上下文内聚，但彻底引入纯领域实体与 repository 会增加映射和接口维护；目前没有足够需求证明这项成本值得承担。
- C 把两件最需要被看见的事显式化：**接口层薄**、**业务模块不感知对话**。这正是当前最模糊的地方（`storefront/` 与 `catalog/` 同级、`commerce_orchestrator/` 与领域模块同级）。
- `application/` 与 `modules/` 的判据明确（§2.1），且能在搬迁时用来切开 `cart/service.py` 里已混杂的两类职责。

**C 放弃了什么**

- 没有纯净的 `domain/` 层：单模块 service 和跨模块动作用例仍持 session factory / `AsyncSession`。纯规则可单测，但事务必须用真实 MySQL 验证；未来存储替换再评估 repository，不提前抽象。
- `application/` 会随时间变大；是否再拆子域按实际职责、变更耦合与测试复杂度判断，不用未经验证的用例数量阈值。
- 包根重命名是一次大范围的机械改动，期间与在途分支的冲突成本高。
- kernel 与 modules 存在分组级互指，但文件级 DTO 依赖保持单向；暂不为彻底消除分组互指复制三个模块的契约。限制是新增共享概念必须检查白名单与运行期循环。
- 跨模块公共服务允许实现间耦合，不等于将来可直接拆微服务；sale_facts 的锁定读取假设共享 MySQL，外部库存/价格服务会破坏本地事务前提，需要重新评审。

**剩余风险**

- **重命名期间的“假绿”**：只比较 skip 数无法证明同一批关键测试被执行；需 §6.2 的 node ID/结果/跳过原因对照及真实服务零跳过门禁。
- **事务拆分风险**：会话门禁外移可能造成竞态，必须先补同事务公共方法与并发测试，再移用例；不可同时调整锁顺序和幂等语义。
- **Alembic 与启动**：模型清单、Docker CMD、工具 import 和静态资源路径均需同步。当前保留源码部署，不把命名空间卫生误称为完成 wheel 打包。
- **`demo/commerce.js` 与前端**：`vite.config.ts` 代理 `/api` 与 `/demo/commerce/guest`，路由前缀不变则无需改动；`api/routes/demo.py` 中 `DEMO_DIR` 的相对路径需相应调整。

## 8. 迁移步骤

默认执行结构阶段；规则修正 R、退役 L 是独立的行为变更。每步必须同时提交该步的引用更新、契约与合同测试；回滚时整体撤销该步，不保留半套新旧装配。本文不授权自动执行迁移或删除旧栈。

| 步 | 动作 | 验证 |
| --- | --- | --- |
| 0 | 固定开发工具版本；新增仅工具部分的 pyproject；建立测试、路由/响应、schema 与配置缺失行为基线 | 保存收集清单、JUnit 结果和 skip 原因；先使关键 MySQL/Redis 基线零跳过，不先配置不存在的 ecom |
| 1 | 顶层建 shared/kernel：拆 Base/ID/time/Principal/hash，迁购物约束 DTO，抽纯规则；保留各路径差异的兼容包装 | 规则/DTO 单测及原响应合同不变；Base 为同一个类，metadata 不分裂；启用 shared 与已修复的 kernel 阶段契约 |
| 2 | DTO/事实依赖解耦：Turn DTO 暂放 commerce_orchestrator/contracts；完成与回放一起解耦；新增 sale_facts 与 kernel 依赖错误，cart 停止引用应用事实读取 | 对旧包名的“业务不得依赖 commerce_orchestrator/models.commerce”契约通过；完成/回放非法输入与普通/锁定报价合同通过 |
| 3 | 先新增同事务公共操作，再迁动作编排；移除全部跨模块 ORM；迁 CommerceError，合并异常处理但保留外部形状 | 跨模块 AST 边界通过；真实 MySQL 事务/并发/checkout 重试测试零跳过；HTTP 错误合同期望不变 |
| 4 | 重构组合根：构造即完整注入图/动作用例/模型；不改变旧推荐栈行为 | 装配实例一致性、缺配置与初始化失败清理测试通过；无 commerce.cart/checkout/model 请求内回填 |
| 5 | 顶层建 modules/application/infra；API 归 routes/contracts；保留的旧栈移 legacy；更新导入、阶段契约与文档 | 使用实际顶层包名的完整现代分层检查、公共入口 AST 与 legacy 边界检查；原路由仍存在 |
| 6 | 包根重命名为 ecom，移除应用 sys.path 补丁；更新 Docker/Alembic/工具/静态定位与文档 | 切换 §6.1 目标配置；新启动入口和导入 smoke test、模型 metadata、全量合同/集成门禁通过，node ID 对照无丢失 |
| 7 | tests 分类、data/scripts → tools；更新 fixture/辅助导入、离线 requirements 与 CLI 文档 | 更新前后 node ID 映射、工具 smoke test、JS 合同测试与三类 CI 门禁通过 |
| R（独立） | 按 §4.2/§4.3 归一可购买性与条件规则，记录每项行为差异 | 负库存/币种/状态/优先级/预算/严格类型测试与三个调用路径一致性；只按批准的差异改变期望 |
| L（条件） | D1/D2 明确确认后实施 §5.5 的退役与清理 | 逐项核对被移除路由、消费者和测试；在独立提交中验收，不用删测试给结构阶段“止血” |

第 1–4 步不依赖大范围目录搬迁，可先获得边界与装配收益；不能在第 2 步就宣称事务/规则风险全部消除。R 在规则抽取及兼容基线就绪后即可独立安排，不要求等待第 7 步；L 不阻塞结构迁移。第 5–6 步只做命名空间/目录与配套入口调整，不夹带功能、格式或退役变更。

### 8.1 分阶段架构配置

| 阶段 | root_packages / 检查入口 | 启用条件 |
| --- | --- | --- |
| 第 0 步 | pyproject 仅含 project/ruff/pytest；暂不添加目标 import-linter 契约 | 不声称架构检查已生效；记录现有违规清单 |
| 第 1–4 步 | 新增 `.importlinter.stage.toml`，枚举仍在顶层的实际应用包；如 cart/catalog/pricing/inventory/shopping_session/commerce_orchestrator/models/api/config/db，以及已建 shared/kernel/sale_facts | 清单包含所有当前应用 Python 包，避免因把中间节点当外部包而漏掉间接依赖；逐步启用已经修复的禁依赖，不使用不存在的 ecom 名称 |
| 第 5 步 | 更新同一阶段配置，根为 api/application/modules/kernel/shared/infra/legacy 及仍存在的 config 等应用包 | 启用全部现代分层、公有入口和 legacy 契约；主链使用实际顶层名字 |
| 第 6 步及以后 | 目标 pyproject 的 `root_packages = ["ecom"]`，删除阶段配置，更新架构测试指向 | 只有 ecom 存在且旧 import 全部更新后才切换；不能根据包缺失自动跳过 |

阶段检查使用 `lint-imports --config .importlinter.stage.toml --no-cache`；目标检查用 `lint-imports --config pyproject.toml --no-cache`。配置与代码搬迁在同一提交更新；AST 检查的文件到角色映射也随阶段更新。已启用的约束不再撤销，未修复违规按明确清单保留至相应步骤，不能用全局 ignore 掩盖。

### 8.2 回滚与验收边界

结构提交不新增表/列，不改变 ID 编码、UTC 类型、JSON 落库形状或幂等请求哈希；临时旧 import 兼容入口只重导出同一个对象，不能复制 Base/异常/DTO。全量调用方切换后移除兼容入口，禁止两套实现并存。

第 5–6 步的回滚同时恢复导入、启动命令、Alembic 模型注册、静态位置与架构配置；因为数据库结构未改，无需数据回迁。R 的回滚只恢复先前规则并重跑记录的行为差异用例；L 的回滚恢复同一版本的旧代码、路由与测试，若已通知外部调用方退役则还需产品侧撤销通知，不能仅凭 Git 撤销宣称完全可回滚。

## 9. 待确认决策

| 编号 | 决策 | 建议 | 影响 |
| --- | --- | --- | --- |
| **D1** | 旧推荐栈是退役还是保留为 legacy？ | 长期倾向退役，但需先核查现有 API 消费者并明确批准；未确认时默认保留。旧栈问题不能当作未经审批删除接口的依据 | 仅控制独立步骤 L，不阻塞结构阶段；明确路由/测试/文档退出清单 |
| **D2** | 实验与指标是否随推荐栈退役？ | 未确认时保持原行为，暂随 legacy 存放；分别核查 experiments/metrics 路由和消费者，不能因推荐退役自动删除 | 若继续服务购物栈，另行评审业务指标归属与契约，不默认新增 experiments 分组 |
| **D3** | `demo/`（原生 HTML/CSS/JS 演示页）是否移入 `ecom/api/static/demo/`？ | **保持顶层**。它与 `frontend/` 同属交付产物，移到包内会让"Python 包"与"静态资源"混在一起 | 决定 `api/routes/demo.py` 的 `DEMO_DIR` 取值 |
| **D4** | `application/` 是否允许直接 import `infra`？ | 本修订采用不允许、只接收注入；代价是 deps 装配代码变长，收益是用例可脱离具体适配器测试 | 若改变此决定，必须同时更新 §2.2、§6.1 的 forbidden/protected 与 AST 规则，不能只改一张表 |
| **D5** | 是否现在执行第 5–6 步（顶层分组 + 包根重命名）？ | 先做 0–4 步，取得事务/合同/装配证据后再定；规则修正 R 与退役 L 独立确认 | 决定代码迁移范围；本次文档修订不等于授权执行这些步骤 |

## 10. 附：目录角色速查

| 目录 | 一句话 |
| --- | --- |
| `ecom/api/` | 只认识 HTTP；不含业务判断 |
| `ecom/application/` | 用例编排；认识会话、轮次、确认门禁 |
| `ecom/modules/` | 领域与事实能力；仅操作自己的表，sessions 提供会话/租约原子能力但不做对话决策 |
| `ecom/kernel/` | 共享词汇与纯规则；仅引用白名单 DTO，不做 I/O |
| `ecom/shared/` | 原语与框架基类；不依赖本工程任何其他分组 |
| `ecom/infra/` | 适配器；只被组合根构造 |
| `ecom/legacy/` | D1/D2 未确认时保留旧调用链；新业务内层不依赖它 |
| `tools/` | 离线导入与运维脚本；不属于应用包 |
| `migrations/`、`frontend/`、`demo/`、`tests/`、`docs/` | 仓库级：迁移、前端产物、演示产物、测试、文档 |

## 11. 评审问题修订对照

| 评审问题 | 修订位置与验收要求 |
| --- | --- |
| Turn DTO 归 API 导致 application 反向依赖；漏迁 TurnReplay | §4.5：用例契约归 application，完成/回放双方校验，落库 JSON 不变 |
| kernel.quote_many 与服务/异常归属冲突 | §2.3、§4.4：kernel 无 I/O，SaleFactsReader 归 modules，依赖错误归 kernel |
| 跨模块 ORM 与动作外移缺事务设计 | §4.7：补完整越界清单、DTO/同事务公共操作、安全时序与真实并发验收 |
| CI 布尔断言错误、间接访问误报、组合根约束遗漏 | §6.1–§6.2：CLI 退出码、直接/间接区分、protected + AST 完整边界、必需 CI 门禁 |
| 不改行为与错误形状/接口退役冲突 | §1.2、§4.1、§5.5：兼容映射不改合同；R/L 独立审批验收 |
| ecom 未建立就检查、skip 数造成假绿 | §6.2、§8.1：阶段配置、测试清单与执行结果映射、真实服务零跳过 |
| 负库存判定与预算函数缺现价输入 | §4.2–§4.3：显式 `< 1`、拆商品/预算函数、边界与优先级回归 |
| P5 只移动 Runtime 未解决回填 | §4.8：无构造环的顺序、构造时依赖完整、资源关闭与实例一致性验收 |
