# CAOS Implementation Status

A dated snapshot of **where implementation stands**, what comes next and where documentation is known to drift from source. Contracts live in [AOSA Architecture Contracts](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md); principles in [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md).

This page is evidence-labelled guidance, not proof. A session must confirm it against `git log`, `git status` and source before relying on it, and report any difference. Update it when a milestone lands; do not regenerate it automatically.

**Snapshot:** 2026-10-29, branch `feature/ops-v2`, `HEAD ac36d84` + uncommitted G1.4 (approved JOSHUA initiation).

## Current milestone

**G1.4 — JOSHUA initiates governed Genesis creation of Enki** (implemented, uncommitted). Seshat L1/L2 and the provisioning chain are committed in `ac36d84`. Enki is **persisted, not awakened**; G1.4 never activates Enki.

**Form-first Enki V2 correction (uncommitted):** the local policy now pins Enki to
`form`, with registered operational role `enki` and existing `empty_eva` personality.
Only the explicit `form/enki` composition gains V2 conversational support, still
requiring the existing chat grants and real backend/Soul checks. The client routes
this composition to a versioned, locally validated Agent-creation draft form and
offers a separate conversation entry. Draft preparation has no execution transport,
submission grant or persistence. Historical chat requests are rejected before
terminal Genesis replay. **Existing live records are unchanged:** local Enki is
still chat/general pending approved recreation. The development API is paused to
prevent bootstrap writes; checks run against isolated storage. See the
[guarded reset proposal](../GENESIS_FORM_ENKI_RESET.md) for exact commands, preserved
records and next-milestone submission requirements.

- `genesis.enki.request@1.0.0` (provider `system.genesis`, `agentverse/agents/genesis/initiation.py`): local-only deterministic initiation through the existing V2 signal endpoint. A distinct static JOSHUA principal has only this grant; no Store/validation grants or trusted lines.
- Server policy pins tenant, authorizing user, JOSHUA, Genesis and Enki. The adapter re-resolves both approvals through UTU, binds a separate Genesis client under the same verified session/user, and reuses `GenesisProvisioner`. Identity, authorization and provenance overrides are rejected.
- Mandatory `genesis.initiation.authorized` evidence (event version 1) precedes provisioning. Its actor is JOSHUA; execution records remain attributed to Genesis. All share the existing operation ID/correlation. Retries under the same initiating authorization reuse its evidence ID; a fresh session records a distinct authorization fact. Recovery after re-authentication preserves earlier successes only when identity, policy and logical fact match; original provenance is never rewritten.
- Entry point: `POST /api/v2/agentverse/agents/{joshua_id}/signals/genesis.enki.request` after verified-session JOSHUA activation, body `{"creation": <Agent Creation Input>}`. Returns the provision result plus `initiation_event_id`, or correlated `in_progress` / `audit_unavailable`. An ID returned before initiation audit succeeds does not imply an admitted operation. Execution is inline; replay requires live authority, not possession of an operation ID.
- Acceptance coverage: `agentverse/tests/agents/test_genesis_initiation.py` and `test_genesis_initiation_integration.py` (real Mongo + disposable Redis). A live verified-operator endpoint check persisted Enki, denied JOSHUA direct Store access and confirmed Enki remained sleeping. Provisioning performs no LLM inference.

- `data.validation@1.0.0` (`agentverse/tools/validation_tool/`): strict, read-only, requester-bound validation of an Agent Creation Input.
- `AgentComposer` (`agentverse/agents/composition/`): deterministic, in-process V2 candidate from a requester-bound validation result.
- `store.create.provisioned@1.0.0` (`agentverse/tools/store_tool/`): creates only tenant-owned identities pinned in the reviewed Genesis policy (`provisioned` slots); confers no `store.create`.
- `GenesisProvisioner` (`agentverse/agents/genesis/`): the trusted coordinator. Takes a CPBX-issued Genesis client, runs validation → composition → provisioned store through CPBX under one OPS V2 managed operation, and records each confirmed outcome with Seshat. G1.4's trusted adapter is its first production caller.
- **Seshat** (`agentverse/audit/`, `coll_logs`): Cognitive Audit and Provenance Service. Strict versioned `CAOSLogRecord`, append-only tenant-isolated repository (identity on built-in `_id`), trusted in-process service; attribution only from UTU permit provenance. Four execution event types plus the initiation event.
- Local policy (`config/genesis.local.json`): JOSHUA (initiation only), Genesis (`data.validation`, `store.create.provisioned`), Enki (form with independent chat grants); JOSHUA/Genesis are deterministic **System Citizens** (`system_citizens`), resolved by UTU's `AgentPrincipalResolver` from the reviewed policy without `coll_agents`; bootstrap seeds no Agent (the legacy Adam bootstrap is removed) and Enki is created only through Genesis. When Enki is missing, startup creates it automatically through the policy `bootstrap` lease (`agents/genesis/bootstrap.py`, `EnkiBootstrap`): UTU → JOSHUA's CPBX client (no cognitive activation) → `genesis.enki.request` → Genesis → Store, with Seshat records marked `origin: bootstrap`; an existing Enki is verified read-only (startup fails on conflict, never overwrites), and retryable outcomes are bounded by a deadline after which startup fails. Historical JOSHUA/Genesis orchestration records are tolerated (must still be the trusted seed) but never read, created or deleted by bootstrap (uncommitted).
- Durability: Store and Seshat writes are separate (not atomic). An operation never succeeds without store evidence; in-process failures leave it retry-pending and an identical retry reconciles idempotently. An attempt interrupted by process death is taken over once its lease expires, resolved to retry-pending by OPS V2 `resolve_reconciled` (never to success), and re-executed by a fresh attempt; verified Seshat records are preserved and only missing evidence is appended (real-Mongo tested).

## Recently completed

| Milestone | Evidence |
| --- | --- |
| Seshat L1/L2 and governed Genesis provisioning | `ac36d84` |
| Store Tool V1 `store.create` | `b8c20ed` |
| OPS2-04C.1 durable admission and discovery | `a98ee98`; [OPS V2 README](../../src/base/operations/v2/README.md) |
| AOSA Engineering Principles (16) | `bd2da90` |
| Genesis v0.3 governed Adam LLM responses (`chat.response`) | `1207b1f`; [GENESIS_LLM_V2](../GENESIS_LLM_V2.md) |
| Genesis v0.2 persistent conversations | [GENESIS_COMMUNICATION_V2](../GENESIS_COMMUNICATION_V2.md) |

## Next agent-creation use case: Genesis (proposed)

Target chain: `JOSHUA → Genesis → data.validation → store.create → Khepri → Enki`.

- **JOSHUA** remains the encompassing operative environment, not a new `BaseAgent` subclass. G1.4 adds a distinct static V2 initiating identity (now a System Citizen with no `Agent` definition) using UTU and the minimal managed runtime; legacy Joshu-A orchestration is unchanged. Enki can be provisioned from the V2 signal interface; there is no Genesis workflow and Enki is not awakened.
- Implemented building blocks: `store.create`, `store.create.provisioned`, `data.validation`, `AgentComposer`, `GenesisProvisioner`, Seshat (`coll_logs`), Khepri activation (for Adam), static UTU approvals/grants, CPBX trusted lines, WFT engine, OPS2-04B managed operations, OPS2-04C.1 admission.
- Missing, in dependency order:
  1. ~~`data.validation`~~ — implemented for `agent` (`ac36d84`); see the [Validation Contract](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md#4-validation-contract). V2 Agent Composition (`agentverse/agents/composition/`, in-process) turns a requester-bound validation result into a `store.create` candidate; it is not a synaptic capability.
  2. ~~A reviewed grant for the Genesis caller~~ — `ac36d84`: local policy grants the Genesis principal only `data.validation` + `store.create.provisioned`, pinned to the tenant-owned Enki slot; Genesis is a local System Citizen (originally seeded like Adam). Non-local Genesis bootstrap remains out of scope.
  3. A persisted, reviewed approval + grant provisioning path (feeding `UTUAuthority.install_policy`) so a newly stored agent can be activated by Khepri. For local Enki the static snapshot already carries its approval/grants; awakening still needs an implemented and tested Khepri activation + verification step.
  4a. ~~Reconcile-outcome commit for interrupted managed attempts~~ — `resolve_reconciled` (`ac36d84`); generic OPS2-04C recovery (workers, background authority) remains open.
  4b. ~~Trusted JOSHUA entry point~~ — G1.4 (uncommitted), approved explicit handoff, initiation evidence and correlated result through the existing V2 signal interface.
  4. UO2 CPBX step runner with structured args and version pinning, so Genesis runs as a governed workflow.
  5. OPS2-04C.2 for deferred/background execution; until then execution stays inline in an authenticated turn.
- Not in scope until requested: wallets, APTREA refinement, channels (#weather Multi-chat Tool).

## Drift register (documentation vs source)

| Item | Documentation says | Source / tooling shows | Status |
| --- | --- | --- | --- |
| Architecture index baseline | Reviewed at `56a2b9c` (`0.1.0-alpha.6`) | Many later milestones (OPS2-04B/C.1, Genesis v0.3, Store Tool) | Index rows updated where touched; full re-review pending |
| Workflow inputs | `WorkflowInputField.required/constraints` read like validation | Only `file` inputs are validated server-side at start | Open — see Workflow Contract §3.2 |
| `ToolSpec.synaptic` | Declares tool capabilities | Not cross-checked against `SynapticRegistry`, no grant derivation | Open — proposal in Tool Contract §2.4 |
| `operations.dispatch.discover` | Registered capability | `register_dispatch_discovery` has no production caller | Expected until the dispatcher exists |
| CAOS research agent | `AGENTS.md` says to use it | `.agents/research.md` is not in an OpenCode `agents/` directory nor a Claude Code subagent | Open — read it as a guide, or link it in a reviewed tooling change |
| AGENTS.md NCSC section | CPBX described as design target | Local CPBX, UTU permits and trusted lines are implemented; listed signals (`personality.infuse`, `prompt.build`, …) remain unregistered | Wording kept; contracts doc states current status |

Add rows when a session finds drift; resolve them through reviewed changes rather than silent edits.
