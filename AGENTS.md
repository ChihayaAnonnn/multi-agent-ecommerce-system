# Repository Guidelines

## Project Structure & Module Organization

`main.py` exposes the FastAPI application. Recommendation agents live in `agents/`; `orchestrator/` coordinates them through the supervisor and LangGraph flow. Put request and response models in `models/`, configuration in `config/`, and supporting logic such as experiments, metrics, and feature storage in `services/`. Tests belong in `tests/` and should mirror the module they cover (for example, `tests/test_ab_test.py`). Architecture and project notes live in `docs/`; `plan.md` records the original plan.

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

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `pip install -r requirements.txt`: install Python dependencies.
- `cp .env.example .env`: create local configuration; set `ECOM_LLM_API_KEY` before calling LLM-backed endpoints.
- `python main.py`: run the API locally on port 8000. Check `GET /health` for a basic startup check.
- `python -m pytest tests/`: run the test suite (install `pytest` separately if needed; it is not listed in `requirements.txt`).
- `docker-compose up -d`: start the container stack; `docker-compose ps` shows service status.

There is no separate build step for the Python application; the `Dockerfile` builds its deployment image.

## Coding Style & Naming Conventions

Use four-space indentation and standard Python naming: `snake_case` for modules, functions, and variables; `PascalCase` for classes. Keep agent implementations in `agents/*_agent.py` and shared data contracts in `models/schemas.py`. Add type hints to new public functions and keep async I/O paths asynchronous. Follow the surrounding style; the repository does not currently configure a formatter or linter.

## Testing Guidelines

Tests use `pytest`-style `test_*` functions in `tests/test_*.py`. Add focused tests for changed behavior, especially experiment assignment, orchestration decisions, and API response shapes. Keep tests deterministic and avoid depending on live LLM, Redis, or Milvus services where a local stub suffices. No coverage threshold is configured.

## Commit & Pull Request Guidelines

Recent history contains an `Initial commit` and a `docs:`-prefixed documentation update. Use a short, imperative subject; a scope prefix such as `docs:` is appropriate when clear. In pull requests, describe the behavior changed, note relevant configuration or service dependencies, link any issue, and include test results. Add request/response examples for API changes and screenshots only for visible UI changes.

## Security & Configuration

Keep secrets in `.env`, which is ignored by Git; update `.env.example` with placeholder values when adding settings. Do not commit API keys or customer data. Check `config/settings.py` before introducing new environment variables.
