# CAOS Development Handoff

**Updated:** 2026-10-30 · **Branch:** `caos-api` `feature/ops-v2` over `828321f`;
`caos-client` `master` over `83c2c48` · **Both working trees contain substantial
uncommitted work — preserve it.**

Purpose: let a fresh conversation (ChatGPT or Claude) continue safely. Ground every claim in
source before acting; this page summarizes, it does not prove. Start sessions with
`/caos-init` (see [`AGENTS.md`](../../AGENTS.md)).

## 1. Current objective and milestone

- **Research objective:** ENKI-1.0 — Enki as a fully functional Cognitive Agent that
  interviews a human, maintains one shared Agent draft and hands an exactly reviewed
  definition to governed creation.
- **Architecture:** [ENKI-1.0 Architecture](../ENKI_1_0_ARCHITECTURE.md), **Approved with
  conditions** 2026-10-30 ([ADR-0003](decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md)).
  Read its §0 first (approved refinements, sequence, open questions).
- **Additional approved decision (2026-10-30, implementation pending):** multi-interface
  Agent navigation — one authorized interface renders directly; several use the existing
  shadcn/ui Tabs, ordered/labelled from registered GUI declarations, opening on the
  declared default and preserving draft/conversation/operation state; no Agent-specific
  tab logic. Enki: Agent Configuration (default), Conversation; Thoth/Nabu: Conversation
  only. See [ENKI-1.0 §0.4](../ENKI_1_0_ARCHITECTURE.md).
- **Decisions 8 and 9 (2026-10-30, approved, implementation pending):** tab labels come
  from the versioned UI Component Registry default, optionally overridden per GUI
  declaration, else a deterministic fallback (presentation only; backward compatible;
  Ptah resolves them eventually). Tabs are implemented in **PTAH-0.2** with shadcn/ui
  Tabs, URL-based selected-tab state where appropriate and server-side access
  revalidation independent of tab visibility. See
  [ENKI-1.0 §0.5–§0.6](../ENKI_1_0_ARCHITECTURE.md).
- **Last completed:** **DRAFT-0.1** (durable revisioned draft), **DRAFT-0.2A** (proposals,
  human selective acceptance, bounded claim recovery, Enki's scoped
  `agent.draft.propose@1.0.0`) and **DRAFT-0.2B** (listing, exact-revision review,
  approval binding with freeze, reopen, confirmed consumption, fork; the Enki form on the
  server draft) — implemented, uncommitted, 0.2B awaiting review; no LLM, no tabs, the
  conversation is not connected — [Agent Drafts](../AGENT_DRAFTS.md).
- **Also completed:** **MANUSCRIPT-0.1** — Manuscript registry, deterministic runtime,
  durable sessions and `agent_creation_interview@1.0.0` (implemented, uncommitted, awaiting
  review; in-process only) — [Manuscripts](../MANUSCRIPTS.md).
- **MANUSCRIPT-0.1A** (approved, implemented, uncommitted, awaiting review): Enki-only,
  scoped `manuscript.session.*@1.0.0` (provider `system.manuscript`, no trusted line) —
  [Manuscripts §6](../MANUSCRIPTS.md). Local policy revision `f79a003f…`.
- **CATALOG-0.1** (accepted) and **CATALOG-0.1A** (implemented, uncommitted, awaiting
  review): governed `ResourceCatalog`; the Manuscript catalog port delegates to it; Enki's
  read-only `catalog.project@1.0.0` (provider `system.catalog`, Enki-only, single-signal
  scope, session origin, no trusted line). Checked-in local policy revision is now
  `1a960bd4…` (adds only Enki's `catalog.project`); not installed while the API is paused —
  [Catalog](../CATALOG.md).
- **VALIDATION-SEC-0.1** (corrected, uncommitted, awaiting review): `data.validation`
  findings no longer name Tools the requester cannot read or reveal their count; no
  decision, digest, grant or binding change. Open related findings VS-1…VS-4 —
  [Security Findings](../SECURITY_FINDINGS.md).
- **REASONING-0.1A** accepted; **REASONING-0.1B** implemented, uncommitted, awaiting review
  ([REASONING-0.1 §13](../REASONING_0_1_ARCHITECTURE.md)): Enki-only scoped
  `cognitive.interpret@1.0.0`, reviewed `config/reasoning.local.json`, checked-in policy
  revision `88cdfa80…`, real-model smoke verified. Next: PTAH interaction contracts →
  ENKI-1.0-B.
- **REASONING-0.1C** (implemented, uncommitted, awaiting review;
  [REASONING-0.1 §14](../REASONING_0_1_ARCHITECTURE.md)): optional cognitive
  `reasoning@1.0.0` Tool over the accepted `cognitive.interpret@1.0.0`; no new grant,
  signal, policy revision or engine. Tool registration ≠ task registration ≠ capability
  authority.
- **Live environment warning (2026-10-31):** the development API was restarted outside the
  task at 23:15 UTC and auto-reloaded uncommitted 0.1B code 9 times (policy `88cdfa80…`
  installed live); it was paused again with operator approval. A read-only Nataraja
  `inspect` (0.1C) found installed = source `88cdfa80…` and no live writes since. Any
  resume now also loads the uncommitted 0.1C Tool (no policy change).
- **Commit state:** everything through MANUSCRIPT-0.1B is committed (`e076639`, plus
  `b7c40fb` Ptah docs); only CATALOG-0.1/0.1A and VALIDATION-SEC-0.1 are uncommitted. Older "uncommitted" labels on
  this page are historical.
- **Next milestone:** **ENKI-1.0-B** (after REASONING-0.1C review and the PTAH interaction
  contracts). Then ENKI-1.0-B →
  PTAH-0.1 → PTAH-0.2 → ENKI-1.0. Ptah's component and interaction **contracts** must be
  designed before ENKI-1.0-B. Nabu activation provisioning is a separate milestone.
- **MANUSCRIPT-0.1B** (implemented, uncommitted, awaiting architectural review): first-class
  optional `manuscript@1.0.0` Tool descriptor/façade; six pinned actions, strict per-Agent
  default reference, safe requester-aware catalog, enabled-startup binding validation,
  V1 denial. Same engine/provider/grants/Enki definition. Designer, validation, isolated
  Sandbox and governed publication are future plans only — [Manuscripts §9–§10](../MANUSCRIPTS.md).
- **Current execution safety:** the operator approved pausing `caos-api` before 0.1B
  source edits to prevent reload/bootstrap against live data. It was found running again at
  CATALOG-0.1 start and re-paused with operator approval (2026-10-30). **Leave it paused for
  architectural review.** Testing uses a separate Docker container and isolated disposable
  Mongo/Redis network; no live DB was queried or modified. Resuming is a separate operator
  decision and can trigger normal reload/bootstrap. Focused regressions: 214 passed.
  Full backend: 2672 passed, 9 skipped, 5 inherited root-signing fixture failures;
  external authz disabled only in the disposable test container, UTU/CPBX active.

## 2. Implemented vs proposed

| Capability | Status | Evidence |
| --- | --- | --- |
| G3.1/G3.2 Agent GUI v1 (`chat`/`form`), Enki draft form presentation | Implemented, uncommitted | `entities/gui_components.py`, `entities/agent_creation_presentation.py`, client `src/app/v2/forms/` |
| G3.3-S scoped UTU/CPBX authority, exact-version dispatch, trusted requester | Implemented, uncommitted | `security/capability_scope.py`, `security/utu.py`, `ncsc/cpbx.py`, [Draft Review](../GENESIS_DRAFT_REVIEW.md) |
| G3.3 validation gateway, review digest, human approval, `effective()` | Implemented, uncommitted | `security/validation_gateway.py`, `agents/creation_review/` |
| G3.4 governed manual creation (`store.create.approved@1.0.0`) | Implemented, uncommitted | `agents/creation_review/creation.py`, [Manual Creation](../GENESIS_MANUAL_CREATION.md) |
| DEV-0.1 Nataraja (inspect, disposable verify, reconcile dry-run) | Implemented, uncommitted | `src/domains/agentverse/nataraja/`, [Nataraja](../NATARAJA.md) |
| DEV-0.2 Genesis/Khepri separation, `/agents/{id}/activation`, runtime route | Implemented, uncommitted | `agents/genesis/activation.py`, `interfaces/api/v2/nataraja_runtime.py` |
| DRAFT-0.1 durable owner-scoped drafts, immutable revisions, CAS `PATCH` | Implemented, uncommitted; used by the Enki form since DRAFT-0.2B | `agents/creation_drafts/`, [Agent Drafts](../AGENT_DRAFTS.md) |
| DRAFT-0.2A proposals, human selective acceptance, claim recovery, Seshat decision evidence, Enki `agent.draft.propose@1.0.0` | Implemented, uncommitted (backend); no runtime turn calls the capability yet | `agents/creation_drafts/{proposal_*,capability}.py`, [Agent Drafts §8–§9](../AGENT_DRAFTS.md) |
| Great Reset | **Not implemented** | — |
| DRAFT-0.2B listing, exact-revision review, approval binding/freeze, reopen, consumption, fork; Enki form on the server draft | Implemented, uncommitted; awaiting review | `agents/creation_drafts/lifecycle.py`, client `src/app/v2/forms/[agentId]/useServerDraft.ts`, [Agent Drafts §11](../AGENT_DRAFTS.md) |
| MANUSCRIPT-0.1 registry, deterministic runtime, sessions, `agent_creation_interview@1.0.0`; 0.1A Enki capability | Implemented, uncommitted; Enki-only scoped signals, not yet called by a turn plan | `src/domains/agentverse/manuscripts/`, [Manuscripts](../MANUSCRIPTS.md) |
| MANUSCRIPT-0.1B first-class optional Tool | Implemented, uncommitted; descriptor/configuration never grants invocation; awaiting review | `tools/manuscript_tool/`, [Manuscripts §9](../MANUSCRIPTS.md) |
| Ptah (incl. tabs), Enki's turn plan using Manuscripts and proposals, the conversation's use of drafts, Enki runtime use of proposals, general reasoning capability | **Approved, not implemented (Planned)** | ADR-0003 |
| Nabu activation provisioning | **Not designed in detail; separate milestone** | ENKI-1.0 §9 |
| Multi-interface Agent navigation (tabs), label resolution | **Approved, implementation pending (PTAH-0.2)**; today the form and conversation are separate routes and GUI v1 has no label field | ENKI-1.0 §0.4–§0.6, client `src/app/v2/forms/[agentId]/page.tsx` |

Paths above are relative to `caos-api/src/domains/agentverse/` unless shown otherwise.

## 3. Last reported environment state (historical, not verified now)

Reported 2026-10-30 by Nataraja and read-only checks; treat as observations that may have
changed. Do not inspect or mutate the live environment merely to refresh this page.

- Local `caos` database contains **Enki** (`…0004`, form/enki/enki, V2), **Thoth**
  (`…0006`, chat/thoth/thoth, V2, created through Enki → Genesis, then separately
  activated) and **Nabu** (`697e8c4b-171a-b672-c90e-f4b6ca5dd05b`, chat/general/empty_eva,
  V2, created through G3.3 approval + G3.4).
- Enki **active** at last inspection (live operator sessions not opened by the agent);
  Thoth **possibly active**; Nabu **sleeping** and not activatable (no UTU approval or
  grants).
- Installed UTU policy matched the checked-in policy (includes `store.create.approved`;
  Thoth handoff `activation: none`). Seven providers runtime-confirmed at 1.0.0.
- 2026-10-30 (MANUSCRIPT-0.1A, Nataraja `inspect`): policy revision `f79a003f…` installed
  (adds Enki's six `manuscript.session.*` grants); installed, checked-in and runtime match;
  all six providers runtime-confirmed as `system.manuscript@1.0.0`; no findings; 3 Agents;
  no Manuscript or draft collections in live `caos`.
- 2026-10-30 (DRAFT-0.2A closeout, Nataraja `inspect`): the reloaded API installed policy
  revision `3e2ea22f…` (adds Enki's `agent.draft.propose`); installed, checked-in and
  runtime versions match; `agent.draft.propose` runtime-confirmed as
  `system.agent_drafts@1.0.0`; no findings; Enki reported sleeping (in-process runtimes do
  not survive reloads); no draft/proposal collections in live `caos`.
- The development API runs `uvicorn --reload` with `src/` mounted: source edits restart
  it and it runs the **uncommitted** code. A restart installs a changed policy revision,
  verifies existing bootstrap Agents read-only, creates a missing Enki, and activates nothing.
- Backend suite: all passing except **5 inherited failures** in
  `src/base/events/v2/tests/test_root_signing_key_wiring.py`. Client suite: 33 inherited
  failures in 8 files unrelated to G3/DEV work.

## 4. Security and lifecycle invariants (must hold in every milestone)

1. Registration, discovery, exposure, attachment and rendered UI never authorize; invocation
   needs a UTU permit plus a CPBX grant or trusted line, rechecked before consequential work.
2. LLM output is an untrusted proposal; it never becomes authority, draft truth or approval.
3. Enki and Manuscript never hold `store.*`; creation only via G3.4 under Genesis's
   creation scope; approval only by an explicit human for an exact review digest.
4. Clients never send executable endpoints, signals, providers, executors, tenants or grants.
5. Genesis owns creation; Khepri owns activation; they are separate requests and evidence.
6. Seshat records consequential transitions with identifiers, revisions, digests and
   causation — never prompts, conversations or secrets. OPS V2 correlates operations.
7. Attenuation only narrows; scoped permits cannot reach unrelated signals.
8. Nothing automatically grants or activates existing Agents (including Nabu).

## 5. Outstanding production-hardening risks

- Open validation findings VS-1…VS-4 (unscoped Genesis `data.validation` via the generic
  route, raw-metadata fingerprints, existence of an unreadable Role requirement, Workflow
  re-read race) — [Security Findings](../SECURITY_FINDINGS.md).
- Revocation racing a write that already passed Store's `effective()` check (bounded window).
- Store writes are not OPS-fenced; an expired-lease process that is still alive cannot be fenced at Store.
- Approval/creation expiry relies on worker clocks.
- JWT role freshness: roles change only on token reissue/session invalidation.
- G3.4 creation idempotency key is per browser tab.
- Partial unique indexes on `coll_agents` are unverified on Cosmos Mongo API.
- Approvals outlive sessions; G3.4 re-verifies, future consumers must too.
- Created Agents have no activation-provisioning path yet.
- MANUSCRIPT-0.1/0.1A: no turn plan or human route uses the signals yet; Tool configuration, groups and
  GUI components are not interviewed; the request-key replay window is 32 per session.
- DRAFT-0.2B: listing indexes unverified on Cosmos DB; consumption is recorded on read
  (not pushed by G3.4); an expired approval keeps a draft frozen until reopened; unsaved
  edits are per tab; the form polls every 15 s.
- DRAFT-0.2A: a crashed decision blocks others for at most its 60 s lease, then is
  completed or released by takeover; no list-proposals endpoint yet; Enki's capability is
  callable only through the trusted in-process adapter (no runtime turn uses it yet).
- DRAFT-0.1: authority is checked before each draft write (a revocation racing an in-flight
  insert can commit that one revision); revision history has no retention policy; no
  Seshat evidence for draft revisions until §0.3 item 4 is decided.
- Inherited signing-key test failures; client inherited failures.
- Mixed-milestone files require hunk-level commits (below).

## 6. Working-tree preservation

- **Do not** reset, stash, checkout, clean or reformat broadly; do not run fix-mode linters
  over the repo. Do not change branches, commit or push without explicit approval.
- Leave the unrelated `.gitignore` change untouched.
- Suggested commit boundaries (when approved): G3.1/G3.2 · G3.3-S · G3.3 · G3.4 · DEV-0.1 ·
  DEV-0.2 · docs. `interfaces/api/v2/genesis_agents.py`, `dependencies/genesis_bootstrap.py`,
  `security/validation_gateway.py`, `audit/models.py`, `dependencies/di_container.py` and
  `config/genesis.local.json` mix milestones and need hunk-level staging (`git add -p`).
- Integration tests need a **disposable** Redis (`UTU_TEST_REDIS_URL`) and
  `OPS2_MONGO_INTEGRATION=1` (Mongo uses throwaway `caos_test_*` databases). Never point
  them at the live Redis. Nataraja `verify` refuses the live Redis server.

## 7. Key files and documentation

- Architecture: [ENKI-1.0](../ENKI_1_0_ARCHITECTURE.md), [ADR-0003](decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md),
  [Contracts](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md), [Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md),
  [Implementation Status](IMPLEMENTATION_STATUS.md), [Index](README.md).
- Genesis guides: [Draft Review](../GENESIS_DRAFT_REVIEW.md), [Manual Creation](../GENESIS_MANUAL_CREATION.md),
  [Nataraja](../NATARAJA.md).
- Drafts (DRAFT-0.1/0.2A): `agents/creation_drafts/{models,repository,service,proposals,proposal_repository,proposal_service,capability}.py`, [Agent Drafts](../AGENT_DRAFTS.md).
- Draft/approval/creation: `agents/creation_review/{models,repository,service,creation}.py`,
  `security/validation_gateway.py`, `interfaces/api/v2/genesis_agents.py`.
- Genesis/Khepri: `agents/genesis/{__init__,initiation,activation,bootstrap}.py`,
  `agents/lifecycle/service.py`, `dependencies/{genesis_bootstrap,genesis_local}.py`,
  `config/genesis.local.json`.
- Enki: `personalities/enki.py`, `agents/roles/enki/spec.py` (`submission_enabled` to be
  removed later), client `src/app/v2/forms/[agentId]/` and `src/shared/lib/enki/`.
- Conversation: `services/cognitive_response_v2.py`, `services/conversation_history_v2.py`.
- Nataraja: `python -m src.domains.agentverse.nataraja {inspect|verify --redis-url …|reconcile --dry-run}`
  inside the API container; manifest `config/nataraja.local.json` (revision 2).

## 8. Restart prompt

```text
Continue CAOS (AOSA) development. Run /caos-init first, then read
docs/architecture/HANDOFF.md and §0 of docs/ENKI_1_0_ARCHITECTURE.md (approved with
conditions, ADR-0003). Both repos (caos-api on feature/ops-v2, caos-client on master) have
large uncommitted G3.1–G3.4, DEV-0.1/0.2, DRAFT-0.1/0.2A/0.2B and MANUSCRIPT-0.1/0.1A/0.1B work: preserve it; do not reset, stash,
commit or push without approval; leave the unrelated .gitignore change alone. The live
environment holds Enki, Thoth and Nabu (historical report); do not create, activate or
reset Agents or touch the live database without explicit approval; use disposable Redis/
Mongo for integration tests. DRAFT-0.1/0.2A/0.2B (docs/AGENT_DRAFTS.md) and
MANUSCRIPT-0.1/0.1A/0.1B (docs/MANUSCRIPTS.md) are implemented and await review. The
development API was paused with operator approval before 0.1B edits: leave it paused
pending architectural review; do not resume/reload live bootstrap as verification.
CATALOG-0.1 (accepted) and CATALOG-0.1A (docs/CATALOG.md) are uncommitted; Enki holds the
approved read-only catalog.project@1.0.0 (checked-in policy revision 1a960bd4…, installed
only when the operator resumes the API). VALIDATION-SEC-0.1 (docs/SECURITY_FINDINGS.md)
is uncommitted and awaits review. REASONING-0.1A/0.1B (accepted) and REASONING-0.1C (docs/REASONING_0_1_ARCHITECTURE.md §14,
reasoning@1.0.0 Tool, awaiting review) are uncommitted; Enki holds the approved cognitive.interpret@1.0.0 (checked-in policy
88cdfa80…, already installed once live by an out-of-task API restart). Next: Ptah interaction
contracts, then ENKI-1.0-B. Enki holds the approved, scoped manuscript.session.*@1.0.0 signals. Preserve
G3.3 digests and G3.4 creation; do not add grants, signals or policy revisions beyond
those approved.
```
