# ADR-0005 — Governed Agent Definition Revision

**Status:** **Approved as the architectural direction, with the corrections in §12**
(2026-10-31).
- **Implemented in source and committed in `c1719b9`** (verified with disposable
  MongoDB/Redis only; nothing enabled live):
  - REV-1, accepted (immutable revision records, digest chaining, read-only baseline
    verification; §13);
  - REV-2, accepted (revision drafts, scoped validation and digest-bound reviews; §14);
  - REV-3, accepted (immutable, digest-bound human approval and withdrawal with Seshat
    evidence; §15);
  - REV-4, conditionally accepted (governed application; source and disposable tests
    only; §16, corrected in §17);
  - REV-5, accepted at the source/test level, subject to deployment verification
    (runtime definition consistency; §17).
- **Not approved:** declaring the revise grant in the checked-in policy, and every live
  operation, including the Enki/Thoth migration. The grant is not installed, Enki and
  Thoth are not migrated, and the development API remains stopped.

**Date:** 2026-10-31

**Evidence base:**
- source at `b70ab0b` on `feature/ops-v2`, plus the then-uncommitted CONV-REC-0.1,
  MANUSCRIPT-0.1C, AGENT-CTX-0.1, CONVERSATION-0.1A and AGENT-DEF-SEC-0.1 work (since
  committed in `c1719b9`);
- source inspection only: no database, runtime or policy was read or changed for this
  ADR;
- live definitions are known only from earlier read-only observations (Handoff §3:
  Enki, Thoth and Nabu with `tools: []`), not re-read here.

**Related:**
- [ADR-0004](ADR-0004-conversation-tool-and-activity-context.md) §11–§12;
- [Agent Definition Governance](../../AGENT_DEFINITION_GOVERNANCE.md);
- [Conversation Tool](../../CONVERSATION_TOOL.md);
- [Genesis Draft Review](../../GENESIS_DRAFT_REVIEW.md) (G3.3) and
  [Genesis Manual Creation](../../GENESIS_MANUAL_CREATION.md) (G3.4);
- [Agent Drafts](../../AGENT_DRAFTS.md), [Security Findings](../../SECURITY_FINDINGS.md),
  [ENKI-1.0](../../ENKI_1_0_ARCHITECTURE.md), [Realm of Ptah](../../PTAH_REALM_ARCHITECTURE.md).

## 1. Context

AGENT-DEF-SEC-0.1 made every governed definition (V2 runtime, a Store creation record or
a trusted seed) immutable through legacy paths. No governed change path exists, so:
- Enki and Thoth converse only through the identity-bound compatibility allowlist; their
  definitions cannot receive an explicit `conversation@1.0.0` attachment;
- Enki's approved Manuscript and Reasoning capabilities are UTU grants with no Tool
  attachment on its definition;
- no governed definition can be corrected at all.

### 1.1 Authoritative writers of `coll_agents` (source evidence)

Paths relative to `src/domains/agentverse/`.

| Writer | Path | Kind | Governed definitions |
| --- | --- | --- | --- |
| V1 create | `AgentService.create_agent` → `DBService.store_agent` | Insert | Cannot create one: `runtime_version` and seed fields are rejected |
| V1 update / delete / collection link | `AgentService`, `DBService.update_agent` / `delete_chat_agent` / `add_collection_to_agent` | Update, delete | Refused since AGENT-DEF-SEC-0.1 (`409 AGENT_DEFINITION_GOVERNED`) |
| Store `store.create` (`provisioned`, `approved`) | `tools/store_tool/store/agent_store.py` → `AgentDefinitionRepository.insert_once` | Insert-only, idempotent (unique partial index on `store.idempotency_scope`) | The only creator of governed definitions |

**No update writer exists for governed definitions.**

### 1.2 Consumers of a definition

| Consumer | Reads | When |
| --- | --- | --- |
| `TrustedAgentPrincipalResolver` | `id`, `creator`, `tenant_id`, `enabled` / `disabled` | Every principal resolution (activation, each signal) |
| `ConversationGate` (dispatcher) and `CognitiveResponseV2` | Runtime, scope, `type`, `role_name`, `personality_key`, `rule_pack_ids`, `prompt`, `llm_type`, `tools`, seed flag | Each turn, read twice (dispatcher, then provider) |
| `effective_gui_components` | `gui_components` (or the reviewed default) | V1 descriptor routes, Nataraja |
| `GenesisActivation` readiness | Backend selector | Activation |
| Nataraja | Identity, shape, `gui_components`, `store`, Conversation attachments | `inspect` / `reconcile` |
| Manuscript, Reasoning, Catalog, draft proposals | **Nothing from the definition**: authority is UTU grants plus scopes | Each call |

**Runtime instances cache no definition.** `ManagedAgentV2` holds only its key and CPBX
connector; `AgentFactoryV2` and `Khepri` do not read definitions. A changed definition is
therefore visible at the next turn without reactivation.

### 1.3 Reusable infrastructure

| Need | Existing contract |
| --- | --- |
| Human-authored candidate | DRAFT-0.1/0.2B durable drafts (immutable revisions, CAS, proposals, approval freeze, consumption) |
| Deterministic validation | `data.validation@1.0.0` under the scoped validation gateway (G3.3-S); `AgentCreationInput` is a complete definition |
| Composition | `AgentComposer` (V2 policy, backends, GUI default); `resolve_conversation` / `ConversationGate` |
| Digest-bound review | `caos.agent_creation_review` snapshot: normalized input, candidate, resolved dependency fingerprints, UTU policy revision, composition policy; `review_digest = sha256(snapshot)` |
| Human approval | `DraftReviewApprovalService` (24 h TTL, idempotency key, revocation, `effective()` re-verifying Seshat evidence, single consumption) |
| Application under a System Citizen | G3.4: `AgentCreationRecord` state machine, Genesis managed operation, two scoped permits, `store.create.approved` re-verifying the approval at the write |
| Evidence | Seshat `CAOSLogService` (deterministic `stable_event_id`, append-only, conflict-safe), `permit_provenance` |
| Single-document CAS, `_id` uniqueness only | AGENT-CTX-0.1 head pattern (MongoDB/Cosmos parity) |
| Inspection | Nataraja `inspect` / `reconcile --dry-run` |

## 2. Decision proposal: the revision model

### 2.1 Identity and history

- **Stable identity:** the Agent ID never changes. A revision never changes `id`,
  `tenant_id`, `creator`, ownership, exposure, `runtime_version` or `type`; changing
  `type` means creating a new Agent.
- **Revision records:** `caos.agent_definition_revision` v1, in a new insert-only
  collection `coll_agent_definition_revisions`.
  - `_id = "{agent_id}:{revision}"`; `revision` is a strictly increasing integer from 1.
  - Each record carries the exact `predecessor {revision, definition_digest}`, the
    complete revisable `definition`, its `definition_digest`, `origin` (`creation` or
    `revision`) and references to its review, approval and application records.
  - It never holds permits, credentials, conversation content or model output.
- **The decision:** inserting `"{agent_id}:{N+1}"` decides which candidate becomes
  revision N+1. `_id` uniqueness guarantees at most one; a competing approved candidate
  for the same predecessor loses with `revision.conflict`, and its approval is spent.
  This needs no secondary or partial index, and history is never rewritten.
- **Digests:**
  - `definition_digest = sha256({contract, agent_id, definition})`;
  - `revision_digest = sha256({contract, agent_id, revision, predecessor, definition_digest})`
    chains each revision to its predecessor.

### 2.2 The Agent document is a materialized projection

**Recommended:** `coll_agents` stays the read model, so every consumer in §1.2 keeps
working unchanged.
- An applied revision updates only the revisable fields plus a new server-managed
  `definition_revision {revision, revision_digest, applied_at}` field.
- The update is one compare-and-set whose filter is the predecessor:
  `definition_revision.revision_digest == predecessor`, or, for the first revision, the
  field being absent and the creation `store.request_digest` matching the baseline.
- **Rejected alternative: readers resolve the revision collection directly.** Every
  consumer, the principal resolver and Nataraja would have to change, and every turn
  would cost an extra read.

### 2.3 Baseline (revision 1) and historical digests

- **Existing records are untouched:** G3.3 approvals, G3.4 creation records, Genesis
  operations, Seshat records and the `store` creation record (including
  `store.request_digest`) are never rewritten.
- **Baseline:** revision 1 is the definition as created. It is recorded lazily when the
  first revision is prepared, as an `origin: creation` record whose `creation`
  reference names `store.request_digest`, the idempotency scope and the creating
  operation.
- **Baseline verification:** `store.request_digest` is
  `sha256({"resource_type": "agent", "definition": Agent(...).model_dump(mode="json")})`.
  Revision 1 is verified only if recomputing it from the current document reproduces it.
  - **Unverified risk:** definitions created before later `Agent` model fields were
    added (for example `gui_components`), or changed through V1 before AGENT-DEF-SEC-0.1,
    may not reproduce it.
  - **Handling:** a mismatch makes the baseline `unverified`. Revising such an Agent
    then requires an explicit, separately approved operator acknowledgment recorded in
    Seshat; there is no silent re-baseline.
  - **Check first:** Nataraja should compute this, read-only, for Enki, Thoth and Nabu
    before any migration (§9, REV-1).

### 2.4 Revision lifecycle

| Step | Contract (proposed) | Reuses |
| --- | --- | --- |
| 1. Candidate | A revision draft targeting `(agent_id, base_revision)`, holding the complete `AgentCreationInput` with `type` fixed | DRAFT model (`target_agent_id`, `base_revision`; separate collection or kind) |
| 2. Validation | `data.validation` over the complete candidate, under a scoped `agent.definition_revision.validate` operation | Validation gateway |
| 3. Review | `caos.agent_definition_revision_review` snapshot (see after this table) | Review envelope; preview limited to `PREVIEW_FIELDS` |
| 4. Approval | The human approves the exact `review_digest` | `DraftReviewApprovalService` semantics, own contract and collection |
| 5. Application | Genesis applies it through a scoped `store.revise.approved@1.0.0` (§3.3) | G3.4 state machine and approval consumption |
| 6. Projection | Insert revision N+1, then the Agent-document compare-and-set | §2.1–§2.2 |
| 7. Evidence | Seshat events (§3.4) | Deterministic event IDs |

The review snapshot binds:
- the target identity and base revision;
- the predecessor `revision_digest`;
- the normalized candidate and the composed candidate, with backend selectors carried
  from the predecessor and never re-derived;
- the field-level diff;
- resolved dependency fingerprints, the UTU policy revision and the composition policy;
- the Conversation composition, including activity contracts checked against the frozen
  Activity Contract Registry.

**Idempotency and recovery** (the G3.4 pattern):
- Identities are deterministic: one approval maps to one application record, one
  Genesis operation and one revision number.
- Retries converge. A crash after inserting the revision but before the compare-and-set
  is completed by replay or by Nataraja-guided reconciliation, which applies the highest
  revision whose predecessor equals the projection.
- A projection that already names the revision is a success. Any other projection
  (superseded) is `revision.conflict` with no write.

## 3. Authority (proposed; every item requires approval, §10)

### 3.1 Proposing and approving

- **Who proposes:** the Agent's human owner (`creator`) in a verified session, through
  a human route. An Agent never authors a revision directly.
  - A later extension may let an Agent such as Enki submit *proposals* into the
    revision draft. That would reuse the DRAFT-0.2A pattern under its own scoped
    signal and separate approval.
- **Who reviews and approves:** the same human, on the exact `review_digest`. A second
  reviewer can be added later without changing the record (an `approvals[]` contract
  version).
- **Revision gateway:** each `(tenant, subject, executor)` is bound by a reviewed
  `revision_gateways` entry in policy configuration, distinct from creation gateways.
  - Eligibility also requires a governed target owned by that subject.
  - Pinned Genesis identities (Enki, Thoth) keep their pins: a revision cannot change
    their `name`, `type`, `runtime_version`, `role_name` or `personality_key`, the same
    check `AgentDefinitionStore` applies at creation.

### 3.2 UTU

- **Root grant:** the executor (the Genesis System Citizen) would hold a new root grant,
  `store.revise.approved`.
- **Scope:** it is invoked only under the scoped operation `agent.definition.revise`
  with an exact binding chain, using two separately scoped permits (validation-only and
  revise-only), exactly like G3.4.
- **What no one gets:** the human holds no grant; human routes authenticate the owner.
  No trusted line is added.
- **Legacy paths:** V1 `PATCH` stays refused (AGENT-DEF-SEC-0.1). The revision
  capability is reachable only from the revision service, never from the generic
  `/signals/{signal}` route without its scope (lesson from VS-1).

### 3.3 Application by the System Citizen

`store.revise.approved@1.0.0` (provider `system.store`) re-verifies at the write:
- the approval is `effective()` and was consumed by exactly this application record;
- the submitted candidate equals the approved snapshot's candidate;
- the predecessor digest equals the current projection;
- governed-definition markers are present and pins hold.

It then inserts the revision record and performs the projection compare-and-set.
Genesis owns the application; Khepri is not involved.

### 3.4 Seshat

| Event | Meaning |
| --- | --- |
| `agent.definition_revision.approved` / `.revoked` | The human decision |
| `agent.definition_revision.authorized` | Application accepted under current authority |
| `agent.definition.revised` | Revision N+1 applied (subject `agent`; metadata: agent, revision, predecessor digest, revision digest, changed field names, attached Tool keys and versions) |
| `agent.definition_revision.failed` | Definitive failure with issue codes |

Metadata never includes the prompt text or Tool configuration values, only digests and
names.

### 3.5 What a revision never does

- It never grants a capability: attaching `manuscript`, `reasoning` or `conversation`
  only declares and narrows. UTU grants are a separate policy decision.
- It never changes policy, trusted lines, approvals or activation state.

## 4. Lifecycle implications

- **Sleeping Agents:** no effect beyond the projection.
- **Active Agents:**
  - **Without restart:** no runtime caches the definition, so the next turn uses the
    new composition (§1.2).
  - **Stale instances:** to make future cached runtimes detectable, `ManagedAgentV2`
    should record the `definition_revision` it was activated under, and Khepri `status`
    and Nataraja should report a mismatch (`runtime.definition_stale`). A future
    runtime that caches composition must re-awaken on mismatch.
- **CPBX:** routes on grants and provider bindings, not on definitions. A new definition
  is "visible" as soon as the projection commits; no registry reload is needed.
  Revisions cannot register providers.
- **In-flight turns:** the dispatcher and the `chat.response` provider read the
  definition separately, so a revision between the two reads could mix compositions.
  Proposed fix:
  - the dispatcher binds the observed `definition_revision` to the turn and records it
    in the turn journal;
  - providers verify the same revision, or fail with retryable `definition_changed`;
  - leases, fencing and recovery are unchanged.
- **Activity contexts:** if a revision removes a declared activity (for example
  `agent.creation_interview@1.0.0`), new `start`/`switch` calls are refused at the
  coordinator. Existing contexts stay readable and closable; nothing is deleted.

## 5. Migration targets (proposed; not executed)

Schemas are as registered at `c1719b9`:
- `ConversationToolConfig`: `modes`, `history`, `activities`;
- `ManuscriptToolConfig`: `default_manuscript`;
- `ReasoningToolConfig`: `tasks`, exact published keys.

Attachments are stored sparse, as `AgentToolRef` already does.

| Agent | `tools` after revision | Unchanged | Grants |
| --- | --- | --- | --- |
| **Enki** (`form/enki/enki`, pinned) | `conversation` with `modes: [context, llm]` and `activities: [agent.creation_interview@1.0.0]`; `manuscript` with `default_manuscript: agent_creation_interview@1.0.0`; `reasoning` with `tasks: [manuscript.answer_extraction@1.0.0]` | Pins, prompt, persisted `gui_components` (form default plus chat), backends | Unchanged: it already holds `chat.context`, `chat.response`, `genesis.thoth.request`, `agent.draft.propose`, the six `manuscript.session.*`, `catalog.project` and `cognitive.interpret` |
| **Thoth** (`chat/thoth/thoth`, pinned) | `conversation` with `modes: [context, llm]` (it holds `chat.context` and `chat.response`), no activities | Everything else | Unchanged |
| **Nabu** | No revision | — | None; never granted automatically |

**Notes:**
- **Enki without Tools:** `catalog.project` and `agent.draft.propose` have no registered
  Tool. Enki keeps using them as grant-only capabilities, recorded as a gap and not
  invented here.
- **Activity check:** the activity key must resolve in the frozen Activity Contract
  Registry at review time, not only at runtime.
- **Explicit modes:** the targets list `modes` explicitly, rather than relying on
  defaults, so the review shows them.

**Allowlist retirement**, per entry:
1. The revision is applied and Nataraja reports `source: tool` for that identity.
2. Runtime verification passes in each declared mode:
   - a `context` turn and an `llm` turn by the owner, or the Genesis readiness probe
     plus one owner-approved `llm` turn;
   - recorded as Seshat or verification evidence.
3. A reviewed code change removes the entry (`legacy.v2.enki`, `legacy.v2.thoth`).
4. **Nabu:** the entry stays until its owner revises it or the architect decides to
   retire it. Nabu holds no grants, so retiring it changes presentation only.
5. The allowlist and its code path are deleted when empty.

## 6. Conversation GUI consistency (decision proposal)

- **Problem:** since AGENT-DEF-SEC-0.1 a new `chat/general` Agent created without the
  Tool presents a Conversation tab that cannot compose (Nataraja
  `conversation.presented_without_composition`).
- **Authoritative rule for new human-reviewed definitions** (G3.3/G3.4 creations and all
  revisions): a declared or defaulted `chat` component requires a valid explicit
  `conversation@1.0.0` attachment.
  - Otherwise the review is ineligible with `gui.conversation_unavailable`.
  - **No auto-attach:** the human adds the Tool in the draft and sees it in the review
    preview.
  - **Compatibility cost:** this changes the composition policy fingerprint in review
    snapshots, so reviews and approvals pending at deployment become `review.stale` and
    must be re-reviewed (24 h approval TTL).
- **Pinned Genesis outputs are untouched:** provisioned Enki and Thoth slots keep
  `creation_compositions` presentation (unchanged candidates, digests and manifest).
  Their conversation comes from the identity allowlist until migrated.
- **Requester-aware presentation:** descriptors return each effective component with
  `availability: available | unavailable` and a coarse reason (`composition_unavailable`
  or `not_authorized`).
  - It reveals no grant, scope, signal or provider. `not_authorized` is shown only to a
    requester who can already read the Agent.
  - Clients render an unavailable tab disabled, or hide it.
- **Ptah:** PTAH-0.2 composes tabs from declared components ∩ available compositions ∩
  requester-visible interactions, and binds `conversation.submit@1.0.0` only to an
  available `chat` component. Presentation never authorizes a turn.

## 7. Security continuity (classification; separate remediations)

| Finding | Classification | Remediation milestone |
| --- | --- | --- |
| V1 Workflow edits change the behavior of governed Agents that reference a Workflow key (DEF-2) | Medium integrity: Agents reference unversioned keys; G3.3 binds Workflow versions only at review | **WF-GOV-0.1:** version-pinned Workflow references in governed definitions; governed Workflow revision; V1 Workflow edits refused for referenced Workflows |
| PDF ingestion: existence disclosure and no per-Agent write check (DEF-4) | Low–moderate (admin-only route) | **V1-INGEST-SEC-0.1:** require `can_write` on the target; uniform not-found |
| Marker-based governed detection (DEF-5) | Low today; strengthened by revision records | **REV-1:** a governed definition is also any Agent with a revision chain; an explicit server-managed `governance` marker written by Store. Separately, `coll_agents` has no unique `id` index |
| VS-1: Genesis `data.validation` reachable unscoped via the generic signal route | Relevant: revision validation must use a scoped permit | Unchanged; revision design avoids it (§3.2) |
| VS-2: raw-metadata fingerprints in review digests | Inherited by revision reviews (needed for content binding) | Unchanged |
| VS-3, VS-4 | Not materially affected; the VS-4 Workflow read race also applies to revision validation | Unchanged |

The revision authority must not be broadened to fix these.

## 8. Alternatives considered (this review)

No earlier deliberation is recorded; these are the options evaluated now.

1. **Mutable Agent document with an audit log only.** Rejected: there is no exact
   predecessor, and history depends on the log.
2. **Readers resolve the revision collection directly** (§2.2). Rejected for now
   because of the cost to every consumer.
3. **Reusing V1 `PATCH` with added checks.** Rejected: it would make a legacy path a
   revision bypass and contradicts AGENT-DEF-SEC-0.1.
4. **An Enki-specific migration script.** Rejected: not Agent-neutral and ungoverned.
5. **Partial patch candidates instead of complete definitions.** Rejected: complete
   candidates reuse `data.validation` unchanged and make digests self-contained.

## 9. Milestones

| Milestone | Scope | New authority or approval |
| --- | --- | --- |
| **REV-1** (Agent-neutral) | `caos.agent_definition_revision` model and digests; insert-only repository; read-only baseline derivation and verification; Nataraja baseline report for Enki, Thoth and Nabu | New collection (approval). No grant, signal or route |
| **REV-2** | Revision drafts, scoped validation, `caos.agent_definition_revision_review` envelope, GUI consistency rule (§6) | New human routes; new validation scope; `revision_gateways` policy configuration (**policy revision**) |
| **REV-3** | Human approval, revocation, Seshat events | New collection, new Seshat event types, routes |
| **REV-4** | Application: `store.revise.approved@1.0.0`, application record, Genesis operation, insert-then-CAS, recovery | **New CPBX signal, new Genesis root grant (policy revision), new scope** |
| **REV-5** | Runtime reconciliation: turn-bound definition revision, `definition_changed`, Khepri/Nataraja stale detection, activity-context behavior | No grant; turn-journal contract change |
| **ENKI-COMP-0.1** | Owner-approved revisions for Enki and Thoth (§5), executed on live data | **Live operation approval** (run Nataraja `inspect` first; the API must be resumed) |
| **CONV-COMPAT-RETIRE-0.1** | Remove the Enki/Thoth allowlist entries after verification; decide Nabu | Code review only |
| Separate | WF-GOV-0.1, V1-INGEST-SEC-0.1, GUI availability projection (PTAH-0.2 prerequisite) | Each its own review |

## 10. Exact approvals required

1. The new collections `coll_agent_definition_revisions`, a revision draft store and the
   revision approvals and applications collections.
2. The new human routes under `/api/v2/agentverse/agents/{agent_id}/definition-revisions…`.
3. The `revision_gateways` policy configuration entries (policy file change, new UTU
   policy revision).
4. The new CPBX signal `store.revise.approved@1.0.0` and a Genesis root grant for it
   (policy revision), usable only under `agent.definition.revise`.
5. The new validation scope `agent.definition_revision.validate`.
6. The new Seshat event types (§3.4).
7. The validation and composition contract change for GUI consistency (§6), which
   invalidates pending reviews.
8. Live execution of the Enki and Thoth migration revisions, and resuming the API.
9. Any later Agent-authored revision proposals (a scoped signal for Enki).

## 11. Consequences

- **Gains:**
  - an exact, immutable, Agent-neutral history;
  - a human digest-bound decision for every change;
  - application by the same System Citizen pattern as creation;
  - no change to existing consumers or historical digests.
- **Costs:**
  - several new collections, events and one new privileged signal;
  - a policy revision;
  - the risk that baselines of older definitions are unverifiable, which needs
    operator acknowledgment.
- **Until REV-4:** governed definitions remain immutable, and the compatibility
  allowlist remains.

## 12. Approval corrections (2026-10-31)

The approval of this direction added five corrections. They take precedence over §2–§4
where they differ.

1. **Four distinct states, never conflated:**
   - an **approved candidate** (a human decision on an exact review digest, REV-3);
   - a **selected successor** (the immutable revision record N+1, decided by `_id`
     uniqueness);
   - an **applied definition** (the `coll_agents` projection names revision N+1, REV-4);
   - a **reconciled runtime** (turns and diagnostics observe revision N+1, REV-5).
2. **A record is not an application.** An inserted revision record proves only that the
   successor was selected, never that the projection was updated. Application status
   comes from the projection and its compare-and-set outcome.
3. **Recovery through the governed operation model.** A failed or interrupted
   application is recovered by its governed operation (the G3.4-style application record
   and the Genesis managed operation: retry-pending, idempotent re-execution, Seshat
   outcome). It is not recovered by an ad-hoc repair or by re-inserting records. §2.4's
   "completed by replay or Nataraja-guided reconciliation" means re-execution of that
   same governed operation; Nataraja only reports.
4. **Historical creation digests are never rewritten or reinterpreted.** Baseline
   verification applies the creation record's own rule. Unexplained differences stay
   `unverified` or `conflicted`; any equivalence rule must be explicit and documented
   (§13). Adopting an unverified baseline is a separate, future governed decision.
5. **Runtime definition pinning never bypasses UTU.** Binding a turn to a definition
   revision (§4) only adds a consistency check. Every turn still needs the current
   permit, grants and composition, and a revision never confers a capability.

## 13. REV-1 outcome (2026-10-31)

Implemented and committed (`c1719b9`), accepted (`src/domains/agentverse/agents/definition_revisions/`).

- **Canonical definition `caos.agent_definition@1`:** every `Agent` model field except
  the runtime-only `commbridge`, serialized in JSON mode, without storage metadata.
  - The field set is pinned and tested against the model: any `Agent` model change
    needs a new canonical schema version.
  - `definition_digest = sha256({contract, tenant_id, agent_id, definition})`, pinned by
    a golden test vector.
- **Revision record `caos.agent_definition_revision` v1:**
  - **Identity and position:** tenant and Agent identity; `revision_id =
    sha256({contract, tenant, agent, rev})` (tenant-qualified, no separator parsing);
    revision 1 to 10 000.
  - **Origin and chaining:** `origin` is `baseline` (exactly revision 1) or `revision`;
    every other revision has the exact `predecessor {revision, revision_digest}`.
  - **Content:** the complete canonical definition plus its schema and digest;
    `baseline {creation evidence copied unchanged, verification}`; bounded correlation
    (operation, correlation and causation IDs).
  - **Chained digest:** `revision_digest` binds identity, position, origin, predecessor,
    schema, definition digest and baseline provenance.
  - **Storage metadata:** `recorded_at` is excluded from both digests and from content
    identity.
  - **Integrity:** strict, frozen and self-verifying on load.
- **Repository:** `coll_agent_definition_revisions`.
  - **Primitives:** only `insert_one` and `find_one` by `_id`, with JSON-serialized
    documents for byte-identical read-back. No update, delete, upsert or index.
  - **Insert rules:** an identical retry returns the stored record; different content
    for the same identity is `revision.conflict`; a later revision requires its exact
    stored predecessor (`revision.predecessor_mismatch`).
  - **Chain:** `chain()` walks and verifies the history by exact lookups.
  - **Not wired:** nothing writes revisions outside tests; no baseline record exists
    anywhere.
- **Baseline verifier** (`baseline.py`), pure and read-only:
  - **Rule `store.agent_definition@1`:** recomputes `store.request_digest` exactly as
    `store.create` v1 did (`Agent(doc).model_dump(mode="json")` without storage
    metadata). This is the only equivalence applied.
  - **`verified`:** a single holder, well-formed evidence naming the document's tenant
    and creator, an equal digest and no unattributed fields.
  - **`unverified`:** a digest mismatch with no evidence of a later write (consistent
    with model evolution), unattributed fields, or invalid under the current model.
  - **`conflicted`:** duplicate holders of the ID (across tenants), provenance naming
    another tenant or creator, an unexplained `definition_revision` marker, or a digest
    mismatch with a post-creation write (`updated_at` / `modified` ≠ `store.stored_at`).
  - **`unavailable`:** missing in the tenant, no creation evidence (V1 and seeded
    definitions), an unsupported or malformed record, or an unreadable store.
  - **Revision 1:** only a `verified` result can produce revision 1 (`baseline_revision`,
    pure).
- **Nataraja:** `inspect` / `reconcile` report a `definition_baseline` resource per
  governed definition.
  - **Status mapping:** `verified` → passed, `unavailable` → deferred, `unverified` →
    warning plus a finding, `conflicted` → failed plus a finding.
  - **Facts:** evidence availability, current definition digest, recorded and recomputed
    digests, duplicate count, modification evidence and `revision_ready`.
  - **Safety:** full documents never leave the observer; nothing is written.
- **Identity findings:**
  - `coll_agents` has no unique `id` index, and `DBService.find_by_id` is
    `find_one({"id"})`, which returns an arbitrary holder if duplicates exist.
  - Store's unique partial index on `store.idempotency_scope` prevents a second holder
    with the same Store scope, but not a scope-less (V1-style) document with the same ID.
  - Store-created IDs are scope-derived UUIDs or policy-pinned, and V1 IDs are random
    UUID4s, so a collision needs a deliberate or pre-existing write.
  - The verifier treats any duplicate as `conflicted`.
- **Not implemented:** approval, application, projection, adoption of unverified
  baselines, routes, signals, grants and policy.
- **Not verified:** Cosmos DB for MongoDB vCore (only `_id` uniqueness is relied on).

## 14. REV-2 outcome (2026-10-31)

Implemented in source and disposable test infrastructure only; committed in `c1719b9`,
accepted. Nothing was authored, reviewed or installed live.

**Revision drafts** (`agents/definition_revisions/drafts.py`, `caos.agent_definition_revision_draft` v1):
- **Ownership:** a verified human owner, a stable tenant and Agent identity, and the exact
  predecessor `{revision, revision_digest, definition_digest, materialized}`.
- **Complete candidates:** every revisable field of `AgentCreationInput`, never a patch.
  Identity, tenant, runtime, ownership, backends, grants and Store fields are rejected.
- **Edits:** each human edit is an immutable draft revision. Inserting N+1 (`_id`
  uniqueness) is the compare-and-set; an identical retry is idempotent; draft identity is
  deterministic (`uuid5` of tenant, owner, Agent and idempotency key).
- **Isolation:** collection `coll_agent_definition_revision_drafts`. No draft mutates
  `coll_agents`, attaches a Tool or grants anything.

**Predecessors:**
- **Stored history:** the latest stored revision, whose definition must equal the
  current projection; otherwise `revision.baseline_conflicted` (`projection.mismatch`).
- **No history yet:** a `verified` creation baseline, **not materialized**. Its digests
  are exactly those revision 1 would have; no read, edit or review creates it.
- **Otherwise refused:** `unverified`, `conflicted` and `unavailable` baselines yield
  `revision.baseline_<status>`.

**Authority** (`gateway.py`): the G3.3 chain.
- **Chain:** a live human session; a reviewed `revision_gateways` binding
  `(tenant, subject) → executor` (a declared System Citizen); the executor's
  session-origin principal attenuated to the new scope `agent.definition_revision.validate`
  (only binding: the existing `data.validation@1.0.0`); rechecks around dispatch.
- **Target:** a governed definition in the requester's tenant whose `creator` is the
  requester and which the requester can read and write. Missing, foreign and hidden
  targets are all `404 revision.not_found`.
- **Route policy:** the existing `agent.write` policy (`agentverse-user` role plus the
  `AGENT_WRITE` permission).

**Validation and review** (`service.py`, `review.py`):
- **Reused:** the unchanged `data.validation` provider, then `AgentComposer` under
  `CURRENT_V2_POLICY`.
- **Revision-specific issues:**
  - `revision.type_immutable`;
  - `revision.pinned_identity` (policy-pinned name, type, Role and Soul);
  - `revision.conversation_required`: a Conversation component requires an explicit,
    valid `conversation@1.0.0`;
  - `revision.conversation_<reason>`, including unregistered activity contracts against
    the frozen Activity Contract Registry;
  - `revision.no_change` and `revision.definition_invalid`.
- **Composed fields:** only the revisable ones are taken from the composition (name,
  system name, type, Role, Soul, prompt, Tools, Workflows, RulePacks, visibility, groups,
  GUI). Everything else is carried from the predecessor; backends are never re-derived.
- **Snapshot (`caos.agent_definition_revision_review` v1):** requester provenance,
  executor and capability; the draft's exact revision and candidate digest; the
  predecessor; normalized input and digest; candidate canonical definition and digest;
  changed fields; resolved dependencies; issues; UTU policy revision and composition
  policy. It is JSON-normalized and holds no time.
- **Digest:** `review_digest = sha256(snapshot)` only when eligible.
- **Storage:** insert-only in `coll_agent_definition_revision_reviews` with `review_id`,
  `validated_at` and `expires_at = +10 min` (the G3.3 lifetime).
- **Envelope:** follows the G3.3 visibility rules (generic messages, `resolved` withheld,
  no backend selectors) and states `authorizes: {approval, revision, application: false}`.
- **Fail-closed currency:** `review_status` and `require_current` (for REV-3) report
  `current`, `ineligible`, `expired`, `stale_candidate` (a newer draft revision) or
  `stale_predecessor`. A predecessor that changes during validation aborts the review.

**Routes** (`interfaces/api/v2/agent_definition_revisions.py`; human sessions only):
- `GET /api/v2/agentverse/agents/{agent_id}/definition-revisions/baseline`
- `POST …/definition-revisions/drafts` (`Idempotency-Key`)
- `GET` / `PATCH …/definition-revisions/drafts/{draft_id}`
- `POST …/definition-revisions/drafts/{draft_id}/reviews`
- `GET …/definition-revisions/reviews/{review_id}`

Paths have three or more segments, so the generic `/{agent_id}/{operation}` route cannot
capture them.

**Policy, source only:** `revision_gateways` is parsed and validated (executor must be a
System Citizen), and `config/genesis.local.json` declares one local binding (owner →
Genesis). UTU `policy_version` digests only grants and trusted lines, so the installed
revision `88cdfa80…` is unchanged and no install is triggered. No grant, trusted line or
signal was added; nothing was installed live.

**Not implemented:** approval and evidence (REV-3), `store.revise.approved` and
application (REV-4), runtime reconciliation (REV-5), and adoption of unverified baselines.

**Open deployment concerns:**
- Cosmos DB for MongoDB vCore is unverified for all revision collections (only `_id`
  uniqueness is relied on).
- DEF-6: `coll_agents` has no unique `id` index. Live checks found no duplicates for
  Enki, Thoth and Nabu.

## 15. REV-3 outcome (2026-10-31)

Implemented in source and disposable test infrastructure only; committed in `c1719b9`,
accepted. No live approval, policy installation or Agent change. REV-3 never applies a
revision.

**Stages, kept distinct:**

| Stage | Status |
| --- | --- |
| Draft authoring | Implemented (REV-2) |
| Deterministic review | Implemented (REV-2) |
| Explicit human approval | Implemented (REV-3) |
| Genesis application | Future (REV-4) |
| Runtime reconciliation | Future (REV-5) |

**Security answers before implementation:**
1. **Gateway freshness (corrected).** `revision_gateways` is not part of the UTU
   `policy_version`, so a changed binding would not supersede permits, and REV-2 reviews
   recorded only the executor ID.
   - **Fix:** reviews now bind a `gateway_fingerprint`, and approvals bind it plus the
     UTU policy revision.
   - **Effect:** a review or approval whose binding fingerprint, executor or policy
     revision differs from current authority is `stale_authority`; a removed binding
     denies outright. See [Security Findings](../../SECURITY_FINDINGS.md).
2. **Review projection (no change needed).** It follows the G3.3 rules: `resolved`
   withheld, generic messages, codes and paths only, preview limited to `PREVIEW_FIELDS`
   (no backend selectors), `normalized` being the requester's own input. A hidden-resource
   test confirms it.

**Approval contract** (`agents/definition_revisions/approval.py`, `caos.agent_definition_revision_approval` v1):
- **Identity:** `approval_id = uuid5(tenant, human, review_id)`, so one decision per human
  per review; replays and concurrent repeats converge.
- **Binds:** the exact review ID and digest, draft ID and revision, candidate and
  candidate-definition digests, the predecessor (revision, revision and definition
  digests, materialized flag), executor, gateway fingerprint and UTU policy revision.
- **Provenance:** decision-time human-session provenance (Agent-origin and bootstrap
  permits cannot reach it).
- **Lifecycle:** `pending → approved → withdrawn`, with `expires_at = +24 h`. The
  `consumed_by` field is reserved for REV-4 and never set.
- **Storage:** `coll_agent_definition_revision_approvals` (insert, then status-guarded
  compare-and-set), separate from revision records; REV-1 history is untouched.
- **Public projection:** the human-safe state and references only. No provenance,
  executor, fingerprint, policy revision or definition digests.

**Lifecycle:**
- **Approve:** fresh gateway authority and route policy → target ownership → the review
  is current (eligible, unexpired, latest draft revision, same predecessor, same
  gateway and policy) and its digest matches → pending record → Seshat
  `agent.definition_revision.approved` → fresh recheck of authority and currency →
  `pending → approved` compare-and-set.
- **Withdraw:** the approving human only; it takes effect (compare-and-set) before its
  evidence is recorded.
- **Status:** read-only `effective` / `reason`.
- **`effective()` (for REV-4):** requires the approved record, exactly matching evidence,
  no withdrawal evidence, no expiry, and the same gateway, policy and predecessor.

**Seshat:**
- **Events:** `agent.definition_revision.approved` and `.withdrawn` (event version 1),
  subject `agent` / Agent ID, correlation = approval ID.
- **Metadata:** identifiers and digests only, never draft content, snapshots, resolved
  dependencies or model output.
- **Event IDs:** deterministic, so retries record identical evidence.
- **Cross-store limitation:** the record and its evidence are separate Mongo documents
  without a transaction.
  - Approval orders evidence before effect, so a crash leaves a non-effective pending
    record that replay completes.
  - Withdrawal orders effect before evidence, so an audit outage never keeps an approval
    usable.

**Routes:** human sessions with the existing `agent.write` policy:
- `POST /agents/{id}/definition-revisions/approvals`
- `GET …/approvals/{approval_id}`
- `POST …/approvals/{approval_id}/withdrawal`

There is no generic signal route.

**Remaining authority for REV-4** (unchanged from §10): `store.revise.approved@1.0.0`, a
Genesis root grant for it (UTU policy revision), the `agent.definition.revise` scope, the
application record and collection, consumption of approvals, the projection
compare-and-set, and live execution approval.

## 16. REV-4 outcome (2026-10-31)

Implemented in source and disposable test infrastructure only; committed in `c1719b9`,
conditionally accepted (corrected in §17). The checked-in policy does **not** grant the new signal, so the installed UTU
policy revision (`88cdfa80…`) is unchanged and nothing was installed or run live.

**Authority (the approved contract):**
- **Signal:** `store.revise.approved@1.0.0`, provider `system.store`.
- **Scope:** `agent.definition.revise`, a single binding.
- **Grant holder:** Genesis only. `genesis_local` accepts it as a further Genesis grant
  set, but the checked-in policy does not declare it.
- **Caller:** the governed application coordinator, which resolves the approval's
  executor in the human's session and attenuates it to the scope.
- **Store-side checks:** the capability requires a session-origin permit with exactly that
  scope, binding chain, entry operation and provider version.
- **Denied:**
  - unscoped generic-route permits;
  - other scopes, such as revision validation;
  - principals without the root grant (they cannot be attenuated);
  - humans, who never call the signal;
  - bootstrap sessions.
- **No new trusted line or Tool grant; no Khepri operation.**
- **Registration:** only when the reviewed policy grants the signal.

**States** (§12 correction 1):

| State | Where | Meaning |
| --- | --- | --- |
| Approval recorded | REV-3 approval | Human decision |
| Application claimed | `coll_agent_definition_revision_applications`, `_id = claim_id(tenant, Agent, predecessor revision digest)`, then the approval's `consumed_by` | One application per predecessor |
| Successor selected | REV-1 record N+1 | Insert-only; an identical retry resolves it |
| Definition applied | `coll_agents` compare-and-set; `definition_revision {revision, revision_digest, definition_digest, applied_at}` | Projection equals revision N+1 |
| Completed, runtime pending | Application record, plus Seshat `agent.definition_revision.selected` / `.applied` | REV-5 reconciles runtimes |

**Claim and withdrawal race:**
- **Before the claim:** the approval must be effective; withdrawal, expiry, a stale
  authority and a stale predecessor all deny.
- **The claim:** inserting the per-predecessor claim, then consuming the approval
  (compare-and-set on `approved` with `consumed_by` empty).
- **Withdrawal wins** if it commits first; the claim is then released (`failed`, nothing
  selected).
- **After consumption:** withdrawal is refused (`revision_approval.consumed`) and expiry
  no longer applies.
- **One candidate per predecessor:** a claim held by a failed or expired worker blocks
  every other candidate (`predecessor_claimed`) until that application completes, or
  definitively fails before selecting.
- **Claim identity** (`application_id`) binds the claim, approval, human, review digest and
  candidate-definition digest.

**Selection and application:**
- **Before writing,** the Store re-verifies:
  - the consumed approval and its Seshat evidence;
  - the immutable review's candidate definition and its digest;
  - **unique** Agent identity across tenants (DEF-6 fails closed);
  - tenant, creator and governed markers.
- **Baseline:** materialized as revision 1 only if the verifier still reproduces the
  exact approved predecessor.
- **Successor N+1:** inserted, or resolved if identical; a competing candidate is
  `successor_conflict`.
- **Projection:** one `update_one` filtered on identity, tenant, the predecessor marker
  and the observed `updated_at`.
  - It sets only the revisable fields plus `definition_revision` and `updated_at`.
  - The `store` creation record, creation digest, identity, ownership, runtime, backends
    and DNA are preserved.
  - It is then re-read and its canonical digest verified.

**Recovery (OPS V2 managed operations, `operation_id = application_id`):**
- **Leases:** attempts are leased and fenced. A live lease returns `in_progress`; an
  expired one is taken over and reconciled to retry-pending, never to failure.
- **Crash after claim:** the next request resumes the same application.
- **Crash after selection:** the next request applies the same selected revision.
- **Crash after the compare-and-set:** the Store recognizes the applied marker
  (`applied: false`) and only receipts are recorded.
- **Missing evidence:** an application whose evidence failed stays `selected` /
  `applied`, is discoverable (`missing_evidence`), and is completed by replay with one
  event per fact.
- **Not atomic:** claim, approval, revision record, projection, operation and Seshat are
  separate documents. Every intermediate state is non-effective or completed by an
  idempotent retry.

**Open:**
- Cosmos DB vCore is unverified.
- DEF-6: no unique `id` index; duplicates fail closed.
- REV-1's baseline verifier reports `conflicted` for an Agent that has a projection marker
  but is checked without its chain. Nataraja should read the chain (REV-5).
- A selected-but-unapplicable revision (e.g. the predecessor changed after selection)
  keeps its claim and needs operator inspection.

**Not done:** runtime reconciliation (REV-5), the Enki/Thoth migration, and declaring the
grant in the checked-in policy.

## 17. REV-5 outcome (2026-10-31)

Implemented in source and disposable test infrastructure only; committed in `c1719b9`,
accepted at the source/test level, subject to deployment verification. No grant, policy,
live data or lifecycle change.

### 17.1 REV-4 invariants: findings and narrow corrections

| # | Finding | Kind | Correction |
| --- | --- | --- | --- |
| 1 | An approval could be consumed after expiring between the effectiveness check and consumption | **Defect** | `consumed_at` is written in the consumption compare-and-set and must precede `expires_at` (validated before writing); otherwise `revision_approval.expired` |
| 2 | Consumption failing on expiry did not release the claim (only withdrawal or consumption did) | **Defect** | Expiry also releases the claim (`failed`, nothing selected) |
| 3 | `consumed_by` named the per-predecessor claim, not the application; a released, re-claimed predecessor made a spent approval look current | Precision | `consumed_by` is the exact `application_id`; a spent approval whose claim was released gets a deterministic `claim_released` / `predecessor_claimed` conflict |
| 4 | The projection precondition was checked only after selection; a detected change left an orphan selected revision behind a claim recorded as unselected (and so re-claimable) | **Defect** | Precondition checked before selection. If a successor is already selected, the Store reports it and the claim is recorded `selected`: held, operator-visible, never released |
| 5 | Compare-and-set fenced on the marker and `updated_at` only | Hardening | Also on `store.request_digest` and every revisable field as read |

Verified unchanged:
- a claim inserted without consumption is resumed;
- a lost response after consumption is resumed under the same application ID;
- withdrawal before consumption wins;
- competing claims have one winner;
- a missing marker matches `definition_revision: null`;
- duplicate identity fails closed.

### 17.2 Turn-bound definitions (the freshness contract)

- **Admission:** `ConversationGate.admit` reads the definition **once** and returns its
  composition (Tools, modes, history) plus a `DefinitionBinding`: Agent, canonical
  `definition_digest`, and the applied `revision` / `revision_digest`, or `null` if never
  revised. A partial legacy record binds by a raw digest; it is still an exact change
  detector.
- **Before execution:** the dispatcher calls `ensure_current` immediately before each
  `chat.context` / `chat.response` request. The `chat.response` provider re-verifies the
  binding against its own single read, which is also the definition the inference runs
  on.
- **On change:** the outcome is `event.error`, `code: definition_changed`,
  `retryable: true`. No capability executes and no transcript is appended.
- **Journal:** successful outcomes carry `definitions: [binding…]`. The CONV-REC-0.1 turn
  journal stores outcomes immutably, so a turn's binding is preserved with its result.
- **Never authority:** UTU permits and grants are rechecked independently on every turn;
  an unchanged definition never substitutes for a revoked grant.

### 17.3 Change and replay semantics

| Situation | Behavior |
| --- | --- |
| Changed before admission | The new turn binds the new definition |
| Changed after admission, before execution | `definition_changed` (retryable); nothing executed |
| Changed after the external effect (e.g. after inference returned) | The outcome commits under its admitted binding; nothing re-executes |
| Retrying the **same** event ID | Replays the immutable journaled outcome (including `definition_changed`); never re-executes |
| Retrying as a **new** event ID | Admits under the current definition |
| Interrupted turn (lease expired) | Replays `interrupted` / outcome `unknown`; never re-executed, whatever the definition did |

"Retryable" therefore means the client may submit a new turn; it never authorizes the
system to replay execution.

**Time-of-check / time-of-use limitation:** the binding is verified at the provider's
read, immediately before inference. A revision applied during inference does not affect
that turn, which ran entirely on the bound definition and journals it. CPBX does not
re-verify the definition: it routes on grants and provider bindings only.

### 17.4 Composition, Activity Context and Nataraja

- **Composition:** a removed Tool or mode applies to every newly admitted turn. Explicit
  and reviewed-compatibility behavior are unchanged.
- **Activity Context:** a new generation (`start` or `switch`) needs the owner kind's
  explicit consent (MIG-FIX-0.1: none means denied). `conversation.v2` consults the
  Agent's **current** definition through an injected callable:
  - explicit Tool: only declared activities;
  - reviewed legacy compatibility: only the entry's reviewed activities (MIG-FIX-0.1;
    previously unrestricted);
  - no composition: none.

  Reads, advances, closes, replays and recovery of decided intents are never blocked.
  The owner's grant check still applies on every call, so an old context retains no
  authority. The activities package still does not depend on the Conversation Tool.
- **Nataraja** (`agents/definition_revisions/inspection.py`): reads the immutable chain
  and application claims.
  - A valid applied revision is `verified` with history `applied`, not a creation
    conflict.
  - **Reported:** `selected_unapplied`, `application.incomplete`,
    `application.missing_evidence`, `chain.broken`, `chain.missing`,
    `projection.unknown_revision`, `projection.direct_mutation`,
    `creation_evidence.changed` and `identity.duplicate`.
  - The creation evidence must still equal the stored baseline's copy. Inspection never
    recovers anything.

### 17.5 Khepri and runtime instances

- `ManagedAgentV2` holds only its key and CPBX connector, and Khepri does not read
  definitions: runtime instances are stateless with respect to definitions (tested).
- There is no per-instance revision to report and no stale instance to restart; each turn
  binds at admission. Nataraja reports `runtime: per_turn`. Nothing restarts or activates
  Agents.

**Open deployment concerns:**
- Cosmos DB for MongoDB vCore is unverified for every revision collection.
- DEF-6: no unique `id` index on `coll_agents`; duplicates fail closed throughout.

**Not done:** live migration and declaring the revise grant (both need approval).
