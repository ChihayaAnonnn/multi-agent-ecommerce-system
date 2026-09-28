# Repository Guidelines

## Project Structure & Module Organization

`main.py` exposes the FastAPI application. Recommendation agents live in `agents/`; `orchestrator/` coordinates them through the supervisor and LangGraph flow. Put request and response models in `models/`, configuration in `config/`, and supporting logic such as experiments, metrics, and feature storage in `services/`. Tests belong in `tests/` and should mirror the module they cover (for example, `tests/test_ab_test.py`). Architecture and project notes live in `docs/`; `plan.md` records the original plan.

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
