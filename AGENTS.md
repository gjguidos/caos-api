# AGENTS.md

Repo-specific notes for AI coding agents. Keep it short. Add only things an agent would otherwise get wrong.

## What this is

`agentverse-api`: FastAPI service ("AgentVerse: LLM Orchestration Platform"). The repo's README still calls itself a "FastAPI Microservice Boilerplate" but the codebase is the AgentVerse application — trust `pyproject.toml`, `src/`, and `docker-compose.yml`, not the README, when they conflict.

Python **3.11+** required (`pyproject.toml` `requires-python = ">=3.11"`, ruff/mypy `target-version = py311`). Dockerfile pins `python:3.11-slim-bookworm`.

## Setup & run

Dependency manager is **uv** (no `requirements.txt`). Install dev deps:

```
uv pip install -e ".[test]"
```

Run locally (FastAPI app object is `src.main:app`):

```
uvicorn src.main:app --reload
# or
python entrypoint.py   # uses port 8000, reload=True only when ENVIRONMENT=Development
```

Full stack (API + Mongo + Redis): `docker-compose up --build`.
Stack with proxy + tool server: `docker-compose --profile full up`. The `mcp-proxy` and `tool-server` services build from sibling repos (`../agentverse-proxy`, `../agentverse-tool-server`) — they will fail without those checkouts.

Mongo is exposed on host port **27018** (not 27017). Container-internal hosts: `mongo`, `redis`. For host-side dev set `REDIS_HOST=localhost`, `DB_URI=mongodb://localhost:27018`.

Env: copy `.env.example` -> `.env`. Toggle `AUTHZ_ENABLED=false` to bypass the external user-management dependency. `ENVIRONMENT=Development` is the only value that enables `/docs`, `/redoc`, `/openapi.json`, and uvicorn reload (see `src/base/utils/env_utils.py`).

## Tests

There is no top-level `tests/` directory. **Tests live next to the code they cover** under `src/**/tests/`:

- `src/domains/agentverse/tests/...`, `src/domains/agentverse/tools/**/tests/`
- `src/domains/green_claim/tests/...`
- `src/base/{logging,decorators/middleware,integrations/mcp,infrastructure/http}/tests/`

Pytest config in `pyproject.toml` (`[tool.pytest.ini_options]`):
- `testpaths = ["src"]` — discovery walks `src/` automatically; pytest works from any cwd.
- `pythonpath = [".", "src"]` — both `from src.base...` and `from base...` import styles resolve. Existing tests use the `from src.` style; match the surrounding file and don't normalize blindly.

```
uv run --extra test pytest                       # full suite, no venv activation needed
uv run --extra test pytest path/to/test_file.py::TestName::test_case
uv run --extra test pytest -k segmenter
uv run --extra test pytest --cov=src --cov-report=term-missing
```

Inside an env that already has the `test` extra, plain `pytest` works.

`pytest-asyncio` is installed; async tests use the explicit `@pytest.mark.asyncio` marker (asyncio_mode is left at the default).

## Lint / typecheck

Ruff is the formatter + linter (`tool.ruff`, line-length 100, extends `F401, I, UP`, `fix = true`). Editor config in `.vscode/settings.json` runs `source.fixAll` + `source.organizeImports` on save.

**`ruff check .` rewrites files.** `fix = true` means a bare `ruff check .` auto-fixes across the whole repo, not just what you touched. Scope it to the paths you edited (`ruff check path/ && ruff format path/`), or check `git status` afterwards and revert the collateral. Same for `ruff format .`: a large slice of the repo is currently unformatted, so a repo-wide format buries your diff.

**`mypy src` does not run.** It aborts during module discovery with `Duplicate module named "config"` (`src/base/config/config.py` vs `src/base/config/text/config.py`) and checks nothing. Long-standing repo-wide issue - don't try to "fix" it as a side quest. To typecheck what you edited:

```
mypy --explicit-package-bases --namespace-packages path/to/file.py
```

mypy config is strict-ish: `disallow_untyped_defs = true`, `strict_optional = true`. New functions must be fully typed.

There is no Makefile, no pre-commit, no `ci.yml` (the workflow is `ci.yml.disabled`). CI on `master` is `build-push-container.yml` — semantic-release + Docker build + Trivy scan + Azure Web App slot deploy. Pushes to `backend-feature/**`, `fullstack-feature/**`, `release/**` trigger `afd-build-deploy.yaml` (build + deploy to nonprod). **No workflow runs tests or lint** — run `pytest` yourself.

## Architecture you need to know before editing

Entrypoint chain: `entrypoint.py` -> `src/main.py` -> `src/base/system/initialize_app.py:initialize_app` -> domain DI extension + auto-router discovery + middleware + exception handlers.

- **DI**: `dependency-injector` (pinned `4.47.1`). Root container in `src/base/dependencies/di_container.py` wires sub-containers from `src/base/dependencies/containers/`. Each domain has its own `dependencies/di_container.py` exporting `extend_container(container)`; new domains must be added to `src/domains/__init__.py:initialize_domains`. Modules using `@inject` must be covered by a `WiringConfiguration` (`wiring_config.modules`).
- **Routing is auto-discovered**: `src/base/routing/auto_routing.py` walks `src/domains/`, imports every non-dunder module, and registers any top-level `router: APIRouter`. The domain folder name becomes the OpenAPI tag unless the router declares its own tags. Do not also call `app.include_router` manually.
- **Domains**: `src/domains/agentverse` (the orchestration core), `src/domains/green_claim`, `src/domains/browser_session` (client for an external browser-runner service). `agentverse` is by far the largest — read `agents/`, `registries/`, `tools/`, `workflows/`, `runtime/` together; they form one system.
- **Orchestration loop**: `agents/orchestrator/universal.py` defines five phases but runs three by default (`analyze`, `plan`, `task`). `refine`, `evaluate` and analyze's LLM brief-shaping are gated on `ORCHESTRATOR_QUALITY_PHASES_ENABLED` (default off) - they cost up to three LLM round-trips per turn without affecting the reply. `agents/orchestrator/quality_phases.py:apply_quality_gate` is the single place the flag is read: it drops the gated handlers from `spec.handlers` (the loop's `handler is None` path records them as no-ops) and sets `extra["allow_llm_brief"]` for analyze. `analyze` and `plan` are load-bearing for `@tool` routing and must not be disabled. While the flag is off, run reports carry no `evaluation` block (only `h_evaluate` writes `ctx["eval"]`), and `ctx["signals"]` stays empty on non-`@tool` turns, so `first_forced_pure_step` resolves from plan steps alone.
- **Tools** (`src/domains/agentverse/tools/`): two registration styles coexist, both landing in `ToolRegistry.register_tool` (`registries/tool_registry.py`):
  - Flat modules (`math_tool.py`, `chat_tool.py`, ...) decorate a `BaseTool` subclass with `@tool_registry.register_tool(key=..., name=...)`.
  - Package tools (`writer_tool/`, `ideation_tool/`, `workflow_tool/`, ...) define a `ToolSpec` (from `tools/spec.py`) in `spec.py` (`specs.py` in `auditor_tool`) and decorate the class with `@tool_registry.bind_spec(<spec>)`. `ToolSpec(enabled=False)` silently skips registration.
  - There is no tool loader: tools register as a side effect of the auto-routing import walk. Keys must already be canonical or registration raises at import time. `tools/README.md` documents the `ui` component schema; `docs/agentverse-tools-guide.md` covers authoring. The `base.py`/`spec.py` contract is shared with `agentverse-tool-server` — keep them in sync.
- **Settings**: aggregated `Settings` instance in `src/base/config/config.py` exposed as `settings`. Sub-configs are pydantic-settings classes loaded from env. Strings/copy live in `settings.texts_new`.
- **Vector store**: ChromaDB in dev (persisted at `./chroma_db`, mounted into the container), Azure AI Search in prod. Switch via env (`AZURE_SEARCH_*` vars).
- **Rate limiting**: `fastapi-limiter` 0.1.6 backed by Redis; FastAPILimiter must be initialized before dependencies fire — see `src/base/dependencies/rate_limiter_dependencies.py`. The root `patch_rate_limiter.py` is a one-shot Docker-time fix script, not part of normal runtime.

## Conventions / gotchas

- Imports use `src.` prefix in app code (`from src.base.config.config import settings`). Tests sometimes drop the `src.` prefix because of `pythonpath = ["src"]`. Don't normalize without checking.
- New files: pyupgrade target is py311; prefer `X | Y`, builtin generics, etc. `run_pyupgrade.py` exists to retrofit older files.
- Commits drive releases: `.releaserc.json` uses Conventional Commits + semantic-release on `master` (and `alpha` prerelease). `feat:` -> minor, `fix:`/`perf:`/`refactor:`/`docs:` -> patch, `chore:` -> no release, `BREAKING CHANGE` footer -> major. `commitlint` config at `.github/config/commitlint.config.js`.
- Deployment: container is pushed to ACR, deployed to Azure Web App slot `production-v2`, then swapped to `production` via gated environments (`nonprod-swap-gate`, `prod-swap-gate`). Per-env settings live in `webapp-settings/{features,nonprod,prod}/appsettings.json` and are applied post-deploy by `.github/actions/configure-settings` — add new env vars to all three.

## Where to look first

- API wiring: `src/main.py`, `src/base/system/initialize_app.py`
- Adding a domain: no doc exists (`docs/adding_new_domain.md` referenced by README is missing) - mirror `src/domains/agentverse/dependencies/di_container.py`, then register in `src/domains/__init__.py`
- AgentVerse internals docs: `docs/agentverse-agents-guide.md`, `docs/agentverse-tools-guide.md`, `docs/agentverse-workflow-run-contract.md`, `docs/agentverse-conversation-history.md`, `docs/AgentVerse_Database_Config.md`, `docs/AgentVerse_Redis_Config.md`
