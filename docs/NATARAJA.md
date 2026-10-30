# Nataraja: Development Environment Supervisor (DEV-0.1)

**Status:** implemented, uncommitted, development-only (DEV-0.1 + DEV-0.2 closeout). Non-destructive: inspection,
disposable verification and dry-run reconciliation. The destructive Great Reset is not
implemented.
**Audience:** engineers operating the local CAOS environment.

Nataraja never writes Mongo or Redis, never creates or activates Agents, never calls an
LLM and never bypasses Genesis, Khepri, UTU, CPBX or Seshat. Every proposed action names
the existing System Citizen or service that must perform it and the authority it needs.

## Commands (inside the API container)

```sh
docker compose exec -T api python -m src.domains.agentverse.nataraja inspect [--json]
docker compose exec -T api python -m src.domains.agentverse.nataraja reconcile --dry-run [--json]
docker compose exec -T api python -m src.domains.agentverse.nataraja verify \
  --redis-url redis://<disposable-redis>:6379/0
```

There is no `caos` executable and only `src/` and `config/` are mounted into the API
container, so the package entry point is the supported command. Exit codes: 0
passed/warning/deferred, 1 failed, 2 blocked or refused. `reconcile` without
`--dry-run` is refused. Reports go to stdout; application logs go to stderr.

A disposable Redis for `verify` can be started on the compose network, then removed:

```sh
docker run -d --rm --name caos-nataraja-redis --network agentverse_net redis:6-alpine
docker rm -f -v caos-nataraja-redis
```

`verify` refuses any URL on the live Redis server, requires `redis://`, and uses Mongo
only through the existing disposable `caos_test_*` database isolation.

## Architecture (`src/domains/agentverse/nataraja/`)

| Module | Responsibility |
| --- | --- |
| `manifest.py` | `caos.nataraja.manifest.v1` model and loader (`config/nataraja.local.json`) |
| `observe.py` | Read-only ports (Mongo `find`/collection names; Redis `HGET`/`SCAN`) and the observation: environment identity, redacted targets, policy parsed by `build_genesis_security`, installed UTU policy, registries, Agents (no prompts), OPS V2 operations (`managed_from_document`), Seshat records (`record_from_document`), runtime presence lease count |
| `plan.py` | Pure, deterministic evaluation → resources, findings, reconciliation plan, digest |
| `report.py` | Canonical JSON and human rendering of `caos.nataraja.report.v1` |
| `verify.py` | Runs the lifecycle integration suites as separate processes on disposable infrastructure |
| `cli.py`, `__main__.py` | Development-only command line |

## Manifest contract

`EnvironmentManifest` (strict, frozen): `revision`, `environment`, `tenant_id`,
`owner_id`, `system_citizens`, `system_services`, `agents`, `manual_agents`. Resource
requirements: `required` (absence fails), `optional`, `deferred` (an expected,
not-yet-executed lifecycle step; never a failure) and `manual` (G3.4). Each Agent
declares identity, type, Role, Soul, runtime, creation path (`genesis_bootstrap` or
`genesis_request`, slot, initiator, signal), exactly one primary GUI and lifecycle
(`sleeping` after creation, `khepri_independent` activation). The manifest grants and
provisions nothing.

**System Citizens vs services (source fact):** only JOSHUA and Genesis are System
Citizens: policy-registered principals with enabled UTU approvals and no Agent
document. UTU, CPBX, Khepri and Seshat are trusted runtime services (DI providers) and
are verified as such: provider declared, plus observed evidence where it exists
(installed tenant policy, parsed grant policy, verified `coll_logs`).

## Genesis lifecycle findings (2026-10-30)

| Question | Finding (source/test evidence) |
| --- | --- |
| Can Enki request `genesis.thoth.request`? | Yes: Enki's grant, the reviewed Enki → Genesis handoff and the Thoth provision pin are configured |
| Must Enki be activated first? | Yes: the signal returns 409 until Enki is awakened through Khepri (`test_recursive_genesis_integration.py`) |
| Grants and trusted lines | Enki `genesis.thoth.request` (+ DRAFT-0.2A `agent.draft.propose`, scoped, provider `system.agent_drafts@1.0.0`; + MANUSCRIPT-0.1A the six `manuscript.session.*`, scoped, provider `system.manuscript@1.0.0`; all declared and runtime-confirmed); Genesis `data.validation`, `store.create.provisioned` (+ G3.4 `store.create.approved`); no trusted lines (the initiation adapter binds a separate Genesis client) |
| CPBX provider | `genesis.thoth.request@1.0.0`, provider `system.genesis`, registered at startup when local handoffs exist (derived from policy; the running process's registry is not observable from a separate process) |
| Role and Soul | `thoth` Role and `thoth` Soul are registered |
| Identity and GUI | Stable ID `…0006`; effective GUI is the single default `conversation` chat |
| Idempotent | Yes: replay returns the same operation; one definition |
| Creation vs activation | **Separated (DEV-0.2).** Root cause of the earlier coupling: the reviewed local Thoth handoff opted into `activation: requester_session`, so `GenesisInitiation` called `GenesisActivation` after a successful provisioning inside the same request. The handoff now uses the supported default `none`; Thoth persists sleeping and is activated only by `POST /agents/{id}/activation`, which names the creating Genesis operation and is verified against its store evidence |
| Seshat chain | initiation → validation → composition → store → activation → readiness under one operation, with causation (`test_recursive_genesis_integration.py`) |

## Runtime observation (DEV-0.2)

`GET /internal/dev/nataraja/runtime` (`interfaces/api/v2/nataraja_runtime.py`) exists only
in local development and only for loopback callers (404 otherwise, including from the
host through the Docker port mapping). It is read-only and reports: a per-process
`boot_id`/`started_at` (new on every reload), build version/revision/channel, the UTU
policy revision the process enforces, Genesis handoff activation modes and every
synaptic registration as `{signal, provider, version}`. Nataraja compares:

| Resource | Meaning |
| --- | --- |
| `provider` | Each granted signal: **declared** provider/version (source constants), **runtime** `confirmed`, `mismatch`, `not_registered` or `unavailable` (no runtime observation; warning, never assumed) |
| `runtime` | Boot/build identity; `runtime.policy_drift` when the process enforces a different revision than the checked-in file; `runtime.activation_coupled` if a loaded handoff couples creation and activation |
| `startup` | What the next API start or source reload does: installs a changed policy revision, verifies existing bootstrap Agents read-only, creates a *missing* bootstrap Agent through JOSHUA → Genesis (`startup.bootstrap_armed`), and never activates an Agent |

Reload safety: the development server runs `uvicorn --reload` with `src/` mounted, so
source edits restart the process. A restart performs only the steps above; with Enki
present it writes nothing and records no evidence (tested: a second bootstrap returns
`present` with unchanged Agents and Seshat). Config-only edits (`config/*.json`) do not
reload the process; `runtime.policy_drift` reveals a stale process.

## Verification results (2026-10-30)

DEV-0.2 `verify` against a disposable Redis: all nine areas passed (adding "Genesis/Khepri
lifecycle separation" and the named "Nabu acceptance" rehearsal). DEV-0.1: all seven areas passed (bootstrap and repeated
bootstrap; initiation; recursive Thoth; UTU authorization and revocation; G3.3 review;
G3.4 manual creation; Nataraja read-only inspection). Full backend suite with
integrations: 2376 passed, 5 failed (pre-existing
`src/base/events/v2/tests/test_root_signing_key_wiring.py`), 9 skipped. Live `caos`
Mongo counts/collections and Redis `DBSIZE` were identical before and after live
inspection.
