# CAOS Implementation Status

A dated snapshot of **where implementation stands**, what comes next and where documentation is known to drift from source. Contracts live in [AOSA Architecture Contracts](exoteric/AOSA_ARCHITECTURE_CONTRACTS.md); principles in [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md).

This page is evidence-labelled guidance, not proof. A session must confirm it against `git log`, `git status` and source before relying on it, and report any difference. Update it when a milestone lands; do not regenerate it automatically.

**Snapshot:** 2026-10-30, branch `feature/ops-v2`, `HEAD 828321f`. Committed since the previous snapshot: G1.4 JOSHUA initiation, Enki bootstrap and form-first Enki (`cc56287`), disposable Mongo test isolation (`efdf6ca`), G2.1 recursive Genesis, governed activation and Souls (`828321f`). Uncommitted: G3.1/G3.2 (declarative GUI components, Enki draft-form presentation), G3.3-S (scoped synaptic authority, exact-version CPBX dispatch, trusted requester) and G3.3 (review envelope and human approval).
**Later reported state (2026-10-30, historical; see [Handoff](HANDOFF.md) §3):** Enki, Thoth and Nabu exist; Enki active and Thoth possibly active at last inspection (operator sessions); Nabu sleeping without activation authority. The snapshot below predates those authorized live operations.
**Runtime (read-only check, 2026-10-30, earlier):** the development API container is running (no longer paused). Live `caos` holds 1 Agent (Enki: `form`/`enki`/`enki`, `runtime_version` v2, 2 GUI components), 4 Seshat records and no approval collection.

## Current milestone

**REASONING-0.1C — First-class Reasoning Tool** (implemented, uncommitted; awaiting
architectural review). See [REASONING-0.1 §14](../REASONING_0_1_ARCHITECTURE.md). Optional
cognitive `reasoning@1.0.0` in `ToolRegistry` over the existing `cognitive.interpret@1.0.0`
(provider `system.reasoning`): one declared action, V1 execution denied, trusted adapter
delegating to the existing governed caller, strict `AgentToolRef.config` (`tasks`: exact
published task versions; narrows only), safe RG-02F/`caos.catalog` projection with task
titles/descriptions (`ToolConfigField.options`, additive). No new engine, task, signal,
grant, trusted line, policy revision or model access; Enki's Tool list unchanged. Startup
validates the descriptor and binding when policy grants the signal. Runtime checkpoint
(read-only Nataraja `inspect`, 2026-10-31): source = installed policy `88cdfa80…`; 3 Agents
(Enki, Thoth sleeping; Nabu manual), 3 succeeded operations, 16 verified Seshat records, no
live writes since the 23:15 restart. Full backend 2901 passed, 11 skipped, 5 inherited
failures. Next after review: Ptah interaction contracts → ENKI-1.0-B.

**REASONING-0.1B — Governed Reasoning capability** (implemented, uncommitted; **accepted**). See [REASONING-0.1 §13](../REASONING_0_1_ARCHITECTURE.md). Enki-only
`cognitive.interpret@1.0.0`, provider `system.reasoning`: single-signal scope, session origin
(bootstrap refused), no trusted line, published fingerprint-pinned tasks only, authority
rechecked before the model call and before releasing a result, trusted deadline, per-Agent
model preference only inside the allow-list. Reviewed `config/reasoning.local.json`
(`openai-gpt-6-astra` only) validated at startup (frozen registry, published fingerprint,
exact real structured-output repositories via a generic `model_catalog_checks` lifespan
hook). Checked-in policy revision `1a960bd4…` → `88cdfa80…`; Nataraja declares the provider.
Opt-in real-model smoke test passed (schema acceptance, parsing, token cap). Full backend
2834 passed, 11 skipped, 5 inherited failures. Next after review: ENKI-1.0-B (after Ptah
interaction contracts).

**REASONING-0.1A — Governed Reasoning Task Foundation** (accepted; uncommitted). See [REASONING-0.1 §12](../REASONING_0_1_ARCHITECTURE.md). In-process
`ReasoningTaskRegistry` (exact keys, pinned fingerprints, freeze), `ReasoningExecutor`
(strict bounded requests, model allow-list, structured-output capability gate, deadline-
bounded attempts, cancellation, opaque failures, no prompt logging), first task
`manuscript.answer_extraction@1.0.0` (fingerprint `06354ea7…`) with deterministic closed-world
verification, and an additive `get_structured_response_async` on the OpenAI/Azure clients and
repository. No signal, grant, policy, DI wiring, turn plan, Ptah or client change. 43 new tests
(fake model); full backend 2802 passed, 9 skipped, 5 inherited failures. Next after review:
REASONING-0.1B (authority contract in §12.6).

**REASONING-0.1 — Cognitive resource architecture review** (design approved with refinements). See [REASONING-0.1 Architecture](../REASONING_0_1_ARCHITECTURE.md).
Proposes `cognitive.interpret@1.0.0` (provider `system.reasoning`) over a closed,
fingerprinted Reasoning Task Registry, the first task `manuscript.answer_extraction@1.0.0`,
model allow-list and budgets, deterministic post-validation, Enki's turn sequence (Manuscript
→ Catalog → Reasoning → Manuscript answer → draft proposal → human), Ptah interaction IDs and
VS-1/VS-2 continuity. **Nothing implemented, registered or granted**; no code, policy or
client change. Next after review: REASONING-0.1A (registry/contracts, no grant).

**VALIDATION-SEC-0.1 — Hidden resource disclosure in `data.validation`** (corrected,
uncommitted; awaiting architectural review). See [Security Findings](../SECURITY_FINDINGS.md).
Requester-facing validation findings named a readable Role's internal required Tools
(and their count); the raw result was reachable through the generic signal route on an
awakened Genesis (the G3.3 envelope already scrubbed messages). Findings now name a
resource only if the requester may READ it, and identical findings collapse. Validity,
codes, paths, digests, G3.3/G3.4, grants and bindings unchanged. 14 new tests (one
reproduces the leak end to end against the pre-correction validator); focused regression
1566 passed; full backend 2759 passed, 9 skipped, 5 inherited failures. Open related
findings VS-1…VS-4 are recorded for review.

**CATALOG-0.1A — Governed Catalog capability** (implemented under the architect's
approval, uncommitted on top of `b7c40fb`; awaiting architectural review). See
[Catalog §2](../CATALOG.md). Enki's read-only `catalog.project@1.0.0` (provider
`system.catalog`): Enki-only grant (pinned in `genesis_local`), single-signal attenuated
scope, session-origin permits only (bootstrap refused), no trusted line (refused at
startup), requester from UTU's trusted session adapter, strict bounded `list`/`resolve`
commands, result equal to the human's own catalog for that source Agent, no mutation.
Registered only when policy grants it; startup fails if granted but unbound. Local policy
revision `f79a003f…` → `1a960bd4…` (computed offline; not installed — the live API is
paused). Compatibility integrity: `compatible` is unconditional; incomplete candidates are
`conditional`, hidden/unknown candidate references give identical field-level findings
without names or counts; G3.3 remains authoritative. Verification (isolated container,
disposable Mongo/Redis): 73 catalog tests incl. real-policy integration; focused
regression 1552 passed; full backend 2745 passed, 9 skipped, 5 inherited root-signing
fixture failures. Inherited static finding: 8 mypy `attr-defined` in `genesis_local.py`
(unchanged from `HEAD`). Next after review: **REASONING-0.1**.

**CATALOG-0.1 — Governed Resource Catalog** (implemented, uncommitted; **accepted** by the
architect). See [Catalog §1](../CATALOG.md). In-process, read-only `ResourceCatalog` over
the existing registries, the Workflow catalog and the Manuscript Registry for Agent Types,
Roles, Souls, Tools, Workflows, RulePacks and Manuscripts; strict `caos.catalog.*` v1.0.0
contracts; separate existence/visibility, compatibility and attachment decisions;
execution authorization never evaluated; Ptah-ready declarative metadata. The Manuscript
catalog port delegates to it (fingerprints and pins unchanged).

**MANUSCRIPT-0.1B — First-class Manuscript Tool** (implemented, uncommitted; awaiting
architectural review). `ToolRegistry` key `manuscript`, Tool version `1.0.0`, optional
Agent-configured deterministic façade over the existing `system.manuscript` provider.
Six exact-version action declarations, strict `default_manuscript` configuration through
existing `AgentToolRef`, safe requester-aware catalog projection, fail-closed enabled
startup binding validation and explicit V1 execution denial. No second engine, new signal,
grant, trusted line, Enki definition/policy or client change. Focused regression: 214
passed, including disposable Mongo/Redis integration. The development API was paused with
operator approval before edits and remains paused; no live database access or deployment
claim. Future Designer/validation/Sandbox/governed publication are planned only.
See [Manuscripts §9–§10](../MANUSCRIPTS.md). Committed in `e076639`; CATALOG-0.1 followed.
Full backend regression in isolated Docker: 2672 passed, 9 skipped, 5 inherited
root-signing fixture failures; external authz disabled in tests only, UTU/CPBX active.

**MANUSCRIPT-0.1A — Governed Manuscript capability** (implemented, uncommitted; awaiting architectural review). See [Manuscripts §6](../MANUSCRIPTS.md): `manuscript.session.start|status|answer|correct|restart|complete@1.0.0`, provider `system.manuscript`, Enki-only grants (pinned), one attenuated scope per signal, session-origin permits, independent provider re-verification, gateway-derived Manuscript visibility, strict identity-free commands, `invoke_manuscript` trusted caller; no trusted line, route, LLM or draft/proposal/Genesis/Khepri handle; handoffs restricted to `agent.draft.propose@1.0.0` and never invoked. Local policy revision `f79a003f…`. Next: **CATALOG-0.1**.

**MANUSCRIPT-0.1 — Registry and deterministic Manuscript Tool** (implemented, uncommitted; awaiting architectural review; in-process only). See [Manuscripts](../MANUSCRIPTS.md): `caos.manuscript` v1 definitions (typed fields with artifact targets, ordered stages, closed bounded type-checked predicates, inert Markdown guidance, deterministic fingerprint); `ManuscriptRegistry` (AOSA `Registry[T]` with exact `id@version` keys, pinned published fingerprints, conflict rejection, freeze); `ManuscriptService` (start with validated prefill, status, typed and requester-scoped answers with non-linear progression, correct, restart, complete into a `caos.agent_creation_draft` artifact); `coll_manuscript_sessions` (revision CAS, request-key replay, state digest, pinned fingerprint); published `agent_creation_interview@1.0.0`. No LLM, route, signal, grant, Seshat event or draft write; the capability followed in MANUSCRIPT-0.1A.

**DRAFT-0.2B — Shared draft integration** (implemented, uncommitted; awaiting architectural review). See [Agent Drafts §11](../AGENT_DRAFTS.md): owner-scoped paginated listing of drafts and proposals (indexes `draft_owner_recent`, `proposal_draft_recent`; Cosmos DB unverified); exact-revision G3.3 review adapter (unchanged digest); approval of the exact current revision through the unchanged G3.3 approval with a content-unchanged `approved` freeze revision; reopen (revokes only an unconsumed approval via the new opt-in `revoke(require_unconsumed=True)`); idempotent `consumed` revision once G3.4 confirms creation; fork. The Enki form (`caos-client` `/v2/forms/{agentId}`) now uses the server draft through a same-origin allowlisted proxy: resume/create, unsaved edits per tab, save with `expected_revision`, per-field rebase with explicit conflicts, proposal selective acceptance, review/approve of the saved revision, unchanged governed creation, consumed read-only state and fork. No grant, signal, policy, LLM, tab or conversation change. MANUSCRIPT-0.1 followed.

**DRAFT-0.2A — Draft proposals and human acceptance (backend)** (accepted with conditions; closeout implemented, uncommitted, awaiting review; no client integration; no LLM). Closeout: Enki's approved `agent.draft.propose@1.0.0` (provider `system.agent_drafts`, Enki-only grant and pin, policy revision `3e2ea22f…`, no trusted line, scoped and session-bound; the generic signal route is denied) and bounded decision claims (60 s lease, takeover completes the claimed decision; stale attempts cannot commit after takeover). DRAFT-0.2B contracts documented in [Agent Drafts §10](../AGENT_DRAFTS.md). See [Agent Drafts §8–§9](../AGENT_DRAFTS.md): `coll_agent_draft_proposals`; human-only `GET …/proposals/{id}` and `POST …/proposals/{id}/decision`; claim → DRAFT-0.1 revision CAS with a digest-bound `cause` → Seshat `agent.draft.proposal.accepted|rejected` (new catalog events) → finalize; selective acceptance, field conflicts, idempotent replay and recovery. Next: **DRAFT-0.2B**.

**DRAFT-0.1 — Durable, server-authoritative Agent draft** (implemented, uncommitted; awaiting architectural review; no client integration). See [Agent Drafts](../AGENT_DRAFTS.md): strict partial `AgentCreationInput` content in the existing `caos.agent_creation_draft` envelope; immutable, digest-chained revisions (`coll_agent_draft_revisions`, `_id = draft_id:revision` as the compare-and-set) with a roll-forward latest-revision projection (`coll_agent_drafts`); `POST/GET/PATCH /api/v2/agentverse/agents/{source}/creation-drafts…` with `expected_revision` and `409 draft.revision_conflict`; owner/tenant/source isolation through the G3.3 gateway's existing human-to-source authority (new read-only `requester()`) plus the existing creator policy. No new UTU grant, CPBX signal, Seshat event, OPS V2 operation or G3.3/G3.4 semantic change. DRAFT-0.2A followed.

**ENKI-1.0 — architecture Approved with conditions (2026-10-30); DRAFT-0.1/0.2A/0.2B and MANUSCRIPT-0.1 implemented (uncommitted), the rest Planned.** [ADR-0003](decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md) and [ENKI-1.0 Architecture §0](../ENKI_1_0_ARCHITECTURE.md) record Ptah (interface composition and integrity), the Manuscript Registry/Tool, one durable shared Agent draft and a general reasoning capability. Sequence: DRAFT-0.1, DRAFT-0.2A, DRAFT-0.2B, MANUSCRIPT-0.1 and MANUSCRIPT-0.1A (implemented, uncommitted) → **CATALOG-0.1 (in-process catalog implemented, uncommitted)** → REASONING-0.1 (next) → ENKI-1.0-B → PTAH-0.1 → PTAH-0.2 → ENKI-1.0; Ptah's component/interaction contracts precede ENKI-1.0-B; Nabu activation provisioning is separate. Continuity: [Handoff](HANDOFF.md). Additional approved decision (implementation pending): multi-interface Agent navigation with existing shadcn/ui Tabs, from registered GUI declarations ([§0.4](../ENKI_1_0_ARCHITECTURE.md)); tab labels resolve declaration override → UI Component Registry default → deterministic fallback, and tabs are implemented in PTAH-0.2 ([§0.5–§0.6](../ENKI_1_0_ARCHITECTURE.md)); both approved, implementation pending.

**DEV-0.2 — Genesis/Khepri lifecycle separation and Nataraja closeout** (implemented, uncommitted). Thoth's local handoff no longer opts into `requester_session`: Genesis persists Thoth sleeping and Khepri activates it only through a separate, separately authorized `POST /agents/{id}/activation` naming the creating Genesis operation. Nataraja now confirms runtime provider registrations, the running process's policy revision and boot/build identity through a dev-only, loopback-only read route, and reports what an API start or source reload would do. Named Nabu acceptance rehearsal added (disposable only). See [Nataraja](../NATARAJA.md).

**DEV-0.1 — Nataraja** (implemented, uncommitted, development-only): read-only environment inspection against `config/nataraja.local.json`, disposable lifecycle verification and dry-run reconciliation. See [Nataraja](../NATARAJA.md). Live inspection (2026-10-30): Enki, JOSHUA, Genesis and all four runtime services pass; Thoth is deferred; the reviewed Thoth handoff couples creation with Khepri activation (`activation: requester_session`).

**G3.4 — Governed manual Agent creation through Genesis** (implemented, uncommitted; awaiting architectural review; no live Agent created). See [Genesis Manual Creation](../GENESIS_MANUAL_CREATION.md): effective approval → independent creation authorization → Genesis under separately scoped permits → `store.create.approved@1.0.0` → one sleeping Agent per approval, with durable idempotent creation records and a complete Seshat chain. Reviewed policy change: Genesis gains the scope-only `store.create.approved` root grant.

**G3.3 — Authoritative draft review and human approval** (closed after verification of expiration, audit attribution and effective-approval evidence; uncommitted; awaiting architectural review before G3.4). See [Genesis Draft Review](../GENESIS_DRAFT_REVIEW.md): validation-only scoped authority for the gateway, `data.validation@1.0.0` pinning, the trusted resource requester, the `caos.agent_creation_review` envelope with its content-bound digest, and durable, idempotent, Seshat-evidenced `DraftReviewApproval` records. An approval grants no creation, activation or grant; G3.4 creation is not implemented.

**G1.4 — JOSHUA initiates governed Genesis creation of Enki** (committed, `cc56287`). Seshat L1/L2 and the provisioning chain are committed in `ac36d84`. G1.4 never activates Enki.

**Form-first Enki V2 correction (uncommitted):** the local policy now pins Enki to
`form`, with registered operational role `enki` and existing `empty_eva` personality.
Only the explicit `form/enki` composition gains V2 conversational support, still
requiring the existing chat grants and real backend/Soul checks. The client routes
this composition to a versioned, locally validated Agent-creation draft form and
offers a separate conversation entry. Draft preparation has no execution transport,
submission grant or persistence. Historical chat requests are rejected before
terminal Genesis replay. The local Enki has since been recreated as `form/enki`
(see the runtime check above), and the development API is running again. See the
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
- Local policy (`config/genesis.local.json`): JOSHUA (initiation only), Genesis (`data.validation`, `store.create.provisioned`), Enki (form with independent chat grants); JOSHUA/Genesis are deterministic **System Citizens** (`system_citizens`), resolved by UTU's `AgentPrincipalResolver` from the reviewed policy without `coll_agents`; bootstrap seeds no Agent (the legacy Adam bootstrap is removed) and Enki is created only through Genesis. When Enki is missing, startup creates it automatically through the policy `bootstrap` lease (`agents/genesis/bootstrap.py`, `EnkiBootstrap`): UTU → JOSHUA's CPBX client (no cognitive activation) → `genesis.enki.request` → Genesis → Store, with Seshat records marked `origin: bootstrap`; an existing Enki is verified read-only (startup fails on conflict, never overwrites), and retryable outcomes are bounded by a deadline after which startup fails. G2.1 Recursive Genesis (uncommitted): the policy also accepts Enki → Genesis → Thoth (`genesis.thoth.request`, Enki's only non-conversational grant; Thoth `chat/general/empty_eva/v2` with only `chat.context`/`chat.response`), persisted sleeping; Khepri activation is a separate request with its own observed readiness evidence (`agents/genesis/activation.py`, DEV-0.2). Thoth here is an ordinary conversational Cognitive Agent, not ADR-0001's proposed privileged knowledge citizen. Enki and Thoth now carry their own mythology-inspired Souls (`enki`, `thoth`); the local Enki was recreated through Genesis with the `enki` Soul (`scripts/migrate_enki_soul.js`, 2026-10-29) and the legacy allowance removed. V2 replies use the deterministic Soul projection and shared operating contract (`project_soul_v2.py`, `V2_OPERATING_CONTRACT`); V1 keeps `PromptMixin`. Thoth now has a dedicated conversational `thoth` Role (knowledge interpretation, evidence labelling, faithful summaries, no tools or actions) instead of `general`. Real-Mongo integration tests use disposable `caos_test_*` databases (`src/base/infrastructure/db/mongoDB/isolation.py`), dropped whole and refused for the configured/system databases and Cosmos. Historical JOSHUA/Genesis orchestration records are tolerated (must still be the trusted seed) but never read, created or deleted by bootstrap (uncommitted).
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
| `ToolSpec.synaptic` | Declares tool capabilities | MANUSCRIPT-0.1B adds optional exact version/provider/contract references and Manuscript-specific startup cross-checks; no generic action resolver or grant derivation | Partial — general Tool binding validation remains proposed |
| `operations.dispatch.discover` | Registered capability | `register_dispatch_discovery` has no production caller | Expected until the dispatcher exists |
| CAOS research agent | `AGENTS.md` says to use it | `.agents/research.md` is not in an OpenCode `agents/` directory nor a Claude Code subagent | Open — read it as a guide, or link it in a reviewed tooling change |
| Form-first Enki docs | `GENESIS_FORM_ENKI_RESET.md` and Contracts §5 say draft submission/validation is a future capability | G3.3 adds server review and human approval; G3.4 adds governed creation (both uncommitted) | Open — update when G3.4 is reviewed |
| Thoth lifecycle | Manifest expects Thoth sleeping after creation, activated independently | DEV-0.2: `THOTH_HANDOFF.activation = "none"`; activation is the separate `/activation` request | Resolved (uncommitted) |
| Contracts: Genesis grants | Contracts and status say Genesis holds only `data.validation` + `store.create.provisioned` | G3.4 local policy adds the scope-only `store.create.approved` | Open — update after G3.4 review |
| AGENTS.md NCSC section | CPBX described as design target | Local CPBX, UTU permits and trusted lines are implemented; listed signals (`personality.infuse`, `prompt.build`, …) remain unregistered | Wording kept; contracts doc states current status |

| Snapshot commit state | Snapshot header and milestone entries say G3.x, DEV-0.x, DRAFT-0.x and MANUSCRIPT-0.1/0.1A/0.1B are uncommitted over `828321f` | `git log`: committed as `e076639` (checkpoint) and `b7c40fb` (Ptah docs); tree was clean before CATALOG-0.1 | Open — refresh the snapshot header and per-milestone "uncommitted" labels in a reviewed docs pass |
| Validation finding text | `ValidationIssue` docstring: messages never echo submitted values | `composition.required_tool_missing` named unreadable internal Tools | Resolved (uncommitted) by VALIDATION-SEC-0.1 |
| Live API pause (2026-10-31) | Handoff: the development API stays paused | Restarted outside this task at 2026-10-30 23:15 UTC; auto-reloaded the uncommitted REASONING-0.1B code 9 times (startup completed each time, so the checked-in revision `88cdfa80…` was installed live and the model check passed); paused again with operator approval | Inspected read-only 2026-10-31 (REASONING-0.1C): installed = source `88cdfa80…`, no live writes after the restart, Enki/Thoth sleeping (in-memory runtimes ended), Nabu present. API remains paused; open only until the operator resumes it |
| Live API state | Handoff §1 says the development API is paused pending review | Found running (started 2026-10-30 21:05 UTC) at CATALOG-0.1 start; paused again with operator approval before edits | Resolved for now (paused) |

Add rows when a session finds drift; resolve them through reviewed changes rather than silent edits.
