# CAOS Implementation Status

A dated snapshot of **where implementation stands**, what comes next and where documentation is known to drift from source. Contracts live in [AOSA Architecture Contracts](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md); principles in [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md).

This page is evidence-labelled guidance, not proof. A session must confirm it against `git log`, `git status` and source before relying on it, and report any difference. Update it when a milestone lands; do not regenerate it automatically.

**Snapshot:** 2026-10-29, branch `feature/ops-v2`, `HEAD 843e2ac` + uncommitted G1 (Genesis provisioning) and Seshat L1/L2 work.

## Current milestone

**G1 — Governed Genesis provisioning of Enki, audited by Seshat** (implemented, uncommitted, awaiting review). Enki can be **persisted**; it is **not awakened** (no Khepri activation of Enki is implemented or tested).

- `data.validation@1.0.0` (`agentverse/tools/validation_tool/`): strict, read-only, requester-bound validation of an Agent Creation Input.
- `AgentComposer` (`agentverse/agents/composition/`): deterministic, in-process V2 candidate from a requester-bound validation result.
- `store.create.provisioned@1.0.0` (`agentverse/tools/store_tool/`): creates only tenant-owned identities pinned in the reviewed Genesis policy (`provisioned` slots); confers no `store.create`.
- `GenesisProvisioner` (`agentverse/agents/genesis/`): the trusted coordinator. Takes a CPBX-issued Genesis client, runs validation → composition → provisioned store through CPBX under one OPS V2 managed operation, and records each confirmed outcome with Seshat. No route or client entry point calls it yet.
- **Seshat** (`agentverse/audit/`, `coll_logs`): Cognitive Audit and Provenance Service. Strict versioned `CAOSLogRecord`, append-only tenant-isolated repository (identity on built-in `_id`), trusted in-process service; attribution only from UTU permit provenance. Four Genesis event types.
- Local policy (`config/genesis.local.json`): Adam (chat), Genesis (`data.validation`, `store.create.provisioned`), Enki (chat, approved before it exists); Genesis seeded locally like Adam, Enki never seeded.
- Durability: Store and Seshat writes are separate (not atomic). An operation never succeeds without store evidence; in-process failures leave it retry-pending and an identical retry reconciles idempotently. An attempt interrupted by process death is taken over once its lease expires, resolved to retry-pending by OPS V2 `resolve_reconciled` (never to success), and re-executed by a fresh attempt; verified Seshat records are preserved and only missing evidence is appended (real-Mongo tested).

## Recently completed

| Milestone | Evidence |
| --- | --- |
| Store Tool V1 `store.create` | `b8c20ed` |
| OPS2-04C.1 durable admission and discovery | `a98ee98`; [OPS V2 README](../../src/base/operations/v2/README.md) |
| AOSA Engineering Principles (16) | `bd2da90` |
| Genesis v0.3 governed Adam LLM responses (`chat.response`) | `1207b1f`; [GENESIS_LLM_V2](../GENESIS_LLM_V2.md) |
| Genesis v0.2 persistent conversations | [GENESIS_COMMUNICATION_V2](../GENESIS_COMMUNICATION_V2.md) |

## Next agent-creation use case: Genesis (proposed)

Target chain: `JOSHUA → Genesis → data.validation → store.create → Khepri → Enki`.

- **JOSHUA** is the operative environment / authenticated admin context, not a `BaseAgent` subclass. Enki's definition can be provisioned by `GenesisProvisioner` (G1, uncommitted); there is no Genesis workflow, Enki is not awakened, and nothing invokes the provisioner from a client yet.
- Implemented building blocks: `store.create`, `store.create.provisioned`, `data.validation`, `AgentComposer`, `GenesisProvisioner`, Seshat (`coll_logs`), Khepri activation (for Adam), static UTU approvals/grants, CPBX trusted lines, WFT engine, OPS2-04B managed operations, OPS2-04C.1 admission.
- Missing, in dependency order:
  1. ~~`data.validation`~~ — implemented for `agent` (uncommitted, awaiting review); see the [Validation Contract](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md#4-validation-contract). V2 Agent Composition (`agentverse/agents/composition/`, in-process, uncommitted) turns a requester-bound validation result into a `store.create` candidate; it is not yet a synaptic capability.
  2. ~~A reviewed grant for the Genesis caller~~ — G1 (uncommitted): local policy grants the Genesis principal only `data.validation` + `store.create.provisioned`, pinned to the tenant-owned Enki slot; Genesis is seeded locally like Adam. Non-local Genesis bootstrap remains open.
  3. A persisted, reviewed approval + grant provisioning path (feeding `UTUAuthority.install_policy`) so a newly stored agent can be activated by Khepri. For local Enki the static snapshot already carries its approval/grants; awakening still needs an implemented and tested Khepri activation + verification step.
  4a. ~~Reconcile-outcome commit for interrupted managed attempts~~ — `resolve_reconciled` (uncommitted); generic OPS2-04C recovery (workers, background authority) remains open.
  4b. A trusted entry point (JOSHUA) that obtains the Genesis client through UTU/CPBX and calls `GenesisProvisioner`.
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
