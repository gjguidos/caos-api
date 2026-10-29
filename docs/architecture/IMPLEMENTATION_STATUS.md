# CAOS Implementation Status

A dated snapshot of **where implementation stands**, what comes next and where documentation is known to drift from source. Contracts live in [AOSA Architecture Contracts](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md); principles in [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md).

This page is evidence-labelled guidance, not proof. A session must confirm it against `git log`, `git status` and source before relying on it, and report any difference. Update it when a milestone lands; do not regenerate it automatically.

**Snapshot:** 2026-10-29, branch `feature/ops-v2`, `HEAD a98ee98` + uncommitted Store Tool V1.

## Current milestone

**Store Tool V1 — `store.create`** (implemented, uncommitted, awaiting review).

- Governed, deterministic, insert-only persistence of an approved `Agent` definition into `coll_agents`. Package `src/domains/agentverse/tools/store_tool/`; tests `src/domains/agentverse/tests/tools/test_store_tool.py`.
- Synaptic-only (`SynapticRegistry`, provider `system.store`, version `1.0.0`), registered at Genesis startup only when the reviewed policy grants `store.create` or has a trusted line to it. Not in the V1 tool catalog.
- Authority comes only from the CPBX-issued UTU permit; tenant/creator/ownership are server-derived; the permit is rechecked at the write boundary.
- Idempotency: unique partial index on `store.idempotency_scope`; agent `id` derived from the scope; identical retries replay, conflicting retries fail, uncertain writes are reported as retryable ambiguity.
- Limitations: Cosmos Mongo API unverified (partial indexes required, fails closed); grants are per signal, not per resource type; no reference validation (that is `data.validation`).

## Recently completed

| Milestone | Evidence |
| --- | --- |
| OPS2-04C.1 durable admission and discovery | `a98ee98`; [OPS V2 README](../../src/base/operations/v2/README.md) |
| AOSA Engineering Principles (16) | `bd2da90` |
| Genesis v0.3 governed Adam LLM responses (`chat.response`) | `1207b1f`; [GENESIS_LLM_V2](../GENESIS_LLM_V2.md) |
| Genesis v0.2 persistent conversations | [GENESIS_COMMUNICATION_V2](../GENESIS_COMMUNICATION_V2.md) |

## Next agent-creation use case: Genesis (proposed)

Target chain: `JOSHUA → Genesis → data.validation → store.create → Khepri → Enki`.

- **JOSHUA** is the operative environment / authenticated admin context, not a `BaseAgent` subclass. **Enki** and a Genesis workflow do not exist in source; `config/genesis.local.json` provisions only Adam, seeded directly by `genesis_local.py`.
- Implemented building blocks: `store.create`, Khepri activation, static UTU approvals/grants, CPBX trusted lines, WFT engine, OPS2-04C.1 admission.
- Missing, in dependency order:
  1. `data.validation` (strict creation input + registry resolution; see the [Validation Contract](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md#4-validation-contract)).
  2. A reviewed grant/trusted line for the Genesis caller to `store.create`.
  3. A persisted, reviewed approval + grant provisioning path (feeding `UTUAuthority.install_policy`) so a newly stored agent can be activated by Khepri.
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
