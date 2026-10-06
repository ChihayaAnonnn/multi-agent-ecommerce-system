# Repository Guidelines

## Communication

Use Simplified Chinese for user-facing explanations, questions, progress updates, and summaries. Keep code identifiers, API names, paths, commands, and error text in their original form; repository comments and documentation should generally use Simplified Chinese.

## Project Structure & Module Organization

后端全部源码在唯一包根 `ecom/` 下，`ecom/main.py` 是组合根：`create_app()` 工厂装配应用、注册路由与中间件，运行时（Runtime）只在 lifespan 中创建。后端按六组分组织，依赖方向单向向内：

| 分组 | 职责 | 一句话判据 |
| --- | --- | --- |
| `ecom/api/` | HTTP 接口层 | 只有它认识 `Request`/`Response`/HTTP 状态码。`deps.py` 装配依赖，`errors.py` 映射领域错误，`middleware.py` 处理 CORS 与客户端 IP，`routes/` 放路由，`contracts/` 放 HTTP 输入模型 |
| `ecom/application/` | 用例编排 | 感知会话、轮次、确认门禁。`shopping/` 是购物 Agent（图、节点、意图、动作用例、事实读取），`storefront/` 是只读投影 |
| `ecom/modules/` | 业务模块（限界上下文） | 不感知 HTTP 与对话。`catalog`、`pricing`、`inventory`、`cart`、`checkout`、`identity`、`sessions`、`events`、`sale_facts`，每个内部保持 `models/schemas/service` 三段式 |
| `ecom/kernel/` | 跨模块共享契约与纯规则 | 无 I/O；只允许引用 catalog/pricing/inventory 的 `schemas`，不得引用任何 `service` |
| `ecom/shared/` | 零业务依赖原语 | 不 import 本工程任何其他分组。异常基类、ID、时间、主体、哈希、ORM 声明式基类 |
| `ecom/infra/` | 适配器 | 数据库引擎、Redis、LLM、宿主结账网关；只由组合根构造 |

`ecom/legacy/` 按 D1/D2 保留旧推荐栈、实验与指标，**新业务层不得依赖它**，只有组合根与三个过渡路由例外。

跨模块只能引用对方的 `service.py` 公共方法、`schemas.py` 与声明为公开的 `ports.py`/`types.py`；禁止直接 import 其他模块的 `models.py`，也禁止通过返回值透传 ORM 对象。分层边界由 `pyproject.toml` 的 `[tool.importlinter]`（由 `[tool.layering]` 生成）与 `tests/test_layering.py`、`tests/test_layering_rules.py` 强制，改动后必须跑：

```
lint-imports --config pyproject.toml --no-cache
python -m pytest tests/test_layering.py tests/test_layering_rules.py tests/test_kernel_contracts.py -q
```

配置在 `ecom/config.py`，测试在 `tests/` 并与被测模块同构。架构与项目说明在 `docs/`（`docs/layering-design.md` 是分层设计稿）；`plan.md` 记录最初的计划。

## E-commerce Reference Repositories

Read [`ecommerce-agent-references/README.md`](ecommerce-agent-references/README.md) before designing a shopping, order, fulfillment, or after-sales feature. It maps each business scenario to specific source files and records the limits of each example. The local checkouts are separate Git repositories under `ecommerce-agent-references/`; they are ignored by this project's Git and may be absent in a fresh checkout. Use the upstream links below if a local checkout is missing.

| Business area | Local reference | Upstream |
| --- | --- | --- |
| End-to-end shopping, intent routing, cart, orders, tracking, returns | `ecommerce-agent-references/e-commerce-agents/` | [nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents) |
| Shopping data contracts, tool boundaries, grounding, session memory | `ecommerce-agent-references/commerce-agents/` | [anthropics/commerce-agents](https://github.com/anthropics/commerce-agents) |
| LangGraph order lookup, shipment tracking, return tools | `ecommerce-agent-references/ecommerce-ai-agent/` | [m-peker/ecommerce-ai-agent](https://github.com/m-peker/ecommerce-ai-agent) |
| Refund approval, policy RAG, resumable support workflow | `ecommerce-agent-references/HelpPilot/` | [poysa213/HelpPilot](https://github.com/poysa213/HelpPilot) |
| Retail policies, tools, tasks, end-to-end agent evaluation | `ecommerce-agent-references/tau2-bench/` | [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) |

Treat these repositories as read-only examples. Verify behavior in their code before adapting it, and implement changes in this project's own modules. Do not assume their demo checkout, payment, refund, or logistics flows are production integrations. Do not stage their nested Git repositories or install and run them unless the task requires it.

## Design & Technology Choices

Before settling on an architecture or technology, identify the requirements and constraints, compare viable alternatives, and document the trade-offs behind the decision. Consider correctness, security, performance, reliability, implementation complexity, maintainability, operational burden, cost, and fit with the existing codebase as applicable. State why the chosen option fits this project, what it gives up, and which assumptions or risks remain; do not present a technology choice without its decision rationale.

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `pip install -r requirements.txt`: install Python dependencies.
- `cp .env.example .env`: create local configuration; set `ECOM_LLM_API_KEY` before calling LLM-backed endpoints.
- `python -m uvicorn ecom.main:create_app --factory --host 0.0.0.0 --port 8000`: run the API locally from the repository root on port 8000. Check `GET /health` for a basic startup check.
- `python -m pytest tests/`: run the test suite (install `pytest` separately if needed; it is not listed in `requirements.txt`).
- `docker-compose up -d`: start the container stack; `docker-compose ps` shows service status.

There is no separate build step for the Python application; the `Dockerfile` builds its deployment image.

## Coding Style & Naming Conventions

Use four-space indentation and standard Python naming: `snake_case` for modules, functions, and variables; `PascalCase` for classes. 领域服务放 `modules/<上下文>/service.py`，跨模块契约放 `kernel/`，HTTP 输入模型放 `api/contracts/`。Add type hints to new public functions and keep async I/O paths asynchronous.

Ruff 已配置（`pyproject.toml`，仅启用 E/F/I），但仓库仍存在大量历史告警；
`tests/fixtures/layering/lint-baseline.json` 冻结了它们，**只阻断新增告警**。
不要为了清零而大范围整理无关格式——改动只应让被改动的行不产生新告警。

## 模块化开发与精确修改

Agent 开发时应避免大规模一次性重构，按模块拆分任务并逐步实现、验证。像手术刀一样精确地修改或开发代码：先理解目标模块的职责、接口与依赖，再将改动限定在完成当前任务所必需的范围内，不顺带重写无关代码、调整目录结构或统一风格。涉及多个模块时，明确必要的联动关系，拆分为边界清晰、可独立审查和验证的小步骤；确需较大范围重构时，先说明必要性、影响范围与分阶段实施方案。

## Testing Guidelines

Tests use `pytest`-style `test_*` functions in `tests/test_*.py`. Add focused tests for changed behavior, especially experiment assignment, orchestration decisions, and API response shapes. Keep tests deterministic and avoid depending on live LLM, Redis, or Milvus services where a local stub suffices. No coverage threshold is configured.

## Commit & Pull Request Guidelines

Recent history contains an `Initial commit` and a `docs:`-prefixed documentation update. Use a short, imperative subject; a scope prefix such as `docs:` is appropriate when clear. In pull requests, describe the behavior changed, note relevant configuration or service dependencies, link any issue, and include test results. Add request/response examples for API changes and screenshots only for visible UI changes.

## Security & Configuration

Keep secrets in `.env`, which is ignored by Git; update `.env.example` with placeholder values when adding settings. Do not commit API keys or customer data. Check `ecom/config.py` before introducing new environment variables.
