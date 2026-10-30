# Agent Drafts: Durable Creation Drafts, Proposals and Lifecycle (DRAFT-0.1 → 0.2B)

**Status:** implemented, uncommitted, on `feature/ops-v2` over `828321f` (`caos-client`
`master` over `83c2c48`): DRAFT-0.1; DRAFT-0.2A (accepted with conditions, closed out);
**DRAFT-0.2B** (shared draft integration, §11) awaiting architectural review. The Enki
Agent Configuration form now uses the server draft. Enki's conversation is **not**
connected to the draft, and no Enki runtime turn calls `agent.draft.propose` yet (no LLM
integration). Manuscript, reasoning and Ptah (including tabs) remain Planned.
**Audience:** engineers changing drafts, G3.3 review/approval, the Enki form or
Manuscript/Ptah integration.
**Design:** [ENKI-1.0 §4](ENKI_1_0_ARCHITECTURE.md) (approved with conditions,
[ADR-0003](architecture/decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md)).
**Next:** MANUSCRIPT-0.1 (ENKI-1.0 §0.2), after DRAFT-0.2B review.

A draft is one human's durable, revisioned work in progress toward one
`AgentCreationInput`, bound to the source Agent whose creation form it belongs to
(Enki). A draft **never** validates, reviews, approves, creates or activates anything;
G3.3 (review/approval) and G3.4 (creation) are unchanged.

## 1. Contract

Source: `src/domains/agentverse/agents/creation_drafts/models.py`.

| Model | Purpose |
| --- | --- |
| `AgentDraftContent` | Partial `AgentCreationInput`: the same 11 fields, each optional while editing. Strict: closed field set (server-owned fields such as `tenant_id`, `creator`, `id` are rejected, never stripped), strict JSON types, bounded lengths and ≤ 128 KiB canonical content. Completeness, naming rules and references are G3.3 validation, not draft rules |
| `AgentDraftCreateRequest` | The existing G3.2/G3.3 envelope `caos.agent_creation_draft` v1 (`resource_type: "agent"`, `creation`) with typed partial content |
| `AgentDraftUpdateRequest` | `caos.agent_draft_update` v1: `expected_revision` (≥ 1), `changes` (partial content), `remove` (field names); `changes` and `remove` are disjoint |
| `AgentDraftRevision` | One committed, immutable revision (`caos.agent_draft_revision` v1) |

`AgentDraftRevision` fields: `draft_id`, `revision`, `tenant_id`, `owner_subject_id`,
`source_agent_id`, `status` (`editing` only in DRAFT-0.1), `content`, `provenance`
(`{"/<field>": {origin: "user", revision}}`, exactly one entry per present field; a value
keeps its provenance until it changes), `content_digest`, `creation_request_digest`,
`parent_digest` (previous revision's digest; `null` only for revision 1), `created_at`,
`committed_at` and `revision_digest` (SHA-256 of canonical JSON over every other field).
Validation recomputes both digests, so a stored document that was altered fails closed
(`draft.corrupt`).

Identity: `draft_id = uuid5(sha256({tenant_id, subject_id, source_agent_id, key}))` from
the creation `Idempotency-Key`; a requester cannot address another owner's identity.

Requester projection (`public()`): `draft_id`, `source_agent_id`, `revision`, `status`,
`draft` (the exact G3.3 review envelope for this revision), `provenance`,
`content_digest`, `created_at`, `updated_at` and `authorizes: {review, approval,
creation, activation} = false`. Tenant, owner, storage identity and chain digests are not
exposed.

## 2. Persistence and concurrency

Source: `agents/creation_drafts/repository.py`; DI provider
`agentverse.agent_draft_repository` on the existing `mongodb_repository`.

| Collection | Identity | Content |
| --- | --- | --- |
| `coll_agent_draft_revisions` | `_id = "<draft_id>:<revision>"` | One immutable document per committed revision; never updated or deleted |
| `coll_agent_drafts` | `_id = draft_id` | Projection of the latest committed revision, for one-document reads |

Content is stored as canonical JSON (`content_json`), because Tool configuration may
contain keys MongoDB cannot store as field names.

- **Commit point (compare-and-set).** An update reads the current revision `n`, requires
  `expected_revision == n`, then inserts revision `n + 1`. The built-in unique `_id`
  admits exactly one insert per revision, so of any number of concurrent writers from
  `n` exactly one commits; the others receive `draft.revision_conflict` with the current
  revision. Nothing is replaced unconditionally and no write is lost. No secondary
  index is needed (same `_id`-only approach as the G3.3 approval store; MongoDB and the
  Cosmos DB Mongo API behave alike).
- **Projection.** After a commit the head moves forward by a replace conditioned on its
  previous revision and digest; it never moves backward. If a process stops between the
  two writes, every read rolls the head forward from the revision log (verifying the
  digest chain), so a committed revision is never hidden and a writer at the stale head
  still conflicts.
- **Creation idempotency.** Revision 1 is inserted under the stable `draft_id`. A replay
  with the same key and body returns the current draft (`created: false`); a different
  body under the same key is `idempotency.conflict`.
- An update that changes nothing commits nothing and returns the current revision.
- No OPS V2 operation: a draft write is one local insert with no external effect or
  long-running work.

## 3. HTTP API

Prefix `/api/v2/agentverse/agents/{source_agent_id}`, in
`interfaces/api/v2/genesis_agents.py` (declared before the generic
`/{agent_id}/{operation}` lifecycle route, which would otherwise capture the `POST`).
All routes require a bearer token and a live Genesis session.

| Method and path | Result |
| --- | --- |
| `POST /creation-drafts` (header `Idempotency-Key`, 16–128 `[A-Za-z0-9_-]`) | `201` new draft (revision 1); `200` replay |
| `GET /creation-drafts/{draft_id}` | `200` current revision |
| `GET /creation-drafts/{draft_id}/revisions/{revision}` | `200` one immutable committed revision |
| `PATCH /creation-drafts/{draft_id}` (`caos.agent_draft_update`) | `200` committed (or unchanged) current revision |

Errors (`detail: {code, retryable}`): `403` denied (no bearer/session, not a creator,
unbound source, unreadable source); `404 draft.not_found` (missing, or another owner's,
tenant's or source's — indistinguishable); `409 draft.revision_conflict` with
`current_revision`; `409 idempotency.conflict`; `422` malformed bodies, unknown or
server-owned fields, client ownership/status/approval metadata, invalid key or UUID;
`503 draft.unavailable` (retryable); `500 draft.corrupt`.

## 4. Ownership and authorization

`AgentDraftService` (`agents/creation_drafts/service.py`) resolves the requester afresh
for **every** operation, with existing mechanisms only:

1. `AgentDraftValidationGateway.requester()` (new, read-only): live human session,
   approved validation-gateway binding for `(tenant, subject, source)`, session-origin
   UTU authority of the bound executor attenuated to the validation scope, the trusted
   `resource_requester`, and READ access to the source Agent. This is exactly the
   authority under which the human may already review drafts for that source; it
   dispatches nothing and the permit is neither returned nor stored.
2. The existing creator policy (`_creator_policy`: `Role.USER`, `agent.write`
   permission, `agent_access.can_create`).
3. Tenant and owner come only from that verified requester (and must equal the
   session's); a stored draft is visible only to its tenant, owner and source.

Human draft operations add no UTU grant, CPBX signal, trusted line or Genesis/Khepri
change. Enki (an Agent principal) has no draft write path; it can only submit proposals
through its approved, scoped `agent.draft.propose` capability (§9). Drafts store no
permits, tokens or credentials.

## 5. Relationship to G3.3/G3.4 (unchanged)

`data.validation@1.0.0`, review digests, `DraftReviewApprovalService.effective()`,
approval ownership/revocation, `store.create.approved@1.0.0`, Genesis operations and
Store idempotency are untouched. A draft's `draft` projection is accepted unchanged by
`/creation-draft/validate`, and reviewing it yields the same digest as the same body sent
directly (tested). Approval still carries the draft body; approval by
`{draft_id, revision, review_digest}` is DRAFT-0.2 (or as decided, §0.3 item 1).

## 6. Verification (2026-10-30)

- `tests/agents/test_creation_drafts.py` (51 tests, real UTU/CPBX/validator gateway
  stack, in-memory Mongo double): contract and server-owned field rejection; invalid
  structures; idempotent creation; revision increments and provenance; immutable
  snapshots and digest chain; tamper detection; stale conflicts; 8 simultaneous updates
  from one revision (exactly one commit, every loser reaches the insert); projection
  roll-forward after a lost write and a missing head; owner/tenant/source isolation;
  fresh fail-closed authority for every operation; no validation/approval/creation
  effect; unchanged G3.3 digest; HTTP status codes and route precedence.
- `tests/agents/test_creation_drafts_integration.py` (4 tests, disposable
  `caos_test_*` Mongo and a disposable Redis): 16 concurrent commits on real MongoDB
  (one winner); persistence across repository instances; immutability; tenant isolation;
  roll-forward; HTTP create/read/patch, 6 simultaneous PATCHes (one `200`, five `409`),
  G3.3 review of the stored draft, re-authentication, non-creator and unbound source
  denial, and no change to Agents, approvals, creation operations, Seshat logs or
  runtime instances.
- Full backend suite with integrations: 2433 passed, 9 skipped; the 5 inherited failures
  in `src/base/events/v2/tests/test_root_signing_key_wiring.py` are unchanged. Ruff clean;
  mypy clean except the motor `with_options(read_preference=…)` stub mismatch shared
  with the G3.3 approval repository.
- The live `caos` database was not written (no draft collections exist there).

## 7. Known limitations and decisions deferred

- **No client integration.** The Enki form, Conversation and Manuscript do not use
  drafts yet (out of scope for DRAFT-0.1).
- **Status** is `editing` only; `reviewed`/`approved`/`consumed`/`abandoned`, the
  `reviews`/`approvals` references and `manuscript_session_id` of the proposed aggregate
  (§4.1) arrive with the milestones that produce them. No delete/abandon or list endpoint.
- **Provenance origin** is `user` or `proposal` (DRAFT-0.2A); a `form` origin is not
  distinguished from `user`.
- **No Seshat events for direct edits**: which human edits are consequential is ENKI-1.0
  §0.3 item 4; proposal decisions are audited (§8).
- **Authorization window**: authority is checked before each write; a revocation racing
  an in-flight insert can still commit that one revision (same bounded window as G3.4).
- Revision history is unbounded per draft (no compaction or retention policy yet).
- Two writers sending identical changes from the same revision: one commits and the
  other gets a conflict (no request-level replay for updates).

## 8. Proposals and human decisions (DRAFT-0.2A)

Source: `agents/creation_drafts/{proposals,proposal_repository,proposal_service}.py`;
collection `coll_agent_draft_proposals` (`_id` = stable proposal ID, content as
canonical JSON). A proposal never changes the draft; only the owner's explicit decision
applies its fields, as a normal DRAFT-0.1 revision.

**Proposal** (`caos.agent_draft_proposal`): `proposal_id = uuid5(sha256({tenant, subject,
agent, draft_id, key}))`; `draft_id`, `base_revision`, typed `changes` (partial
`AgentDraftContent`) and `remove` (field names), display-only `rationale` (≤ 2000 chars,
never applied or audited), `origin: enki`, `proposer_agent_id`, the proposer's UTU
`authorization` provenance, `base_fingerprints` (value fingerprint of each proposed field
at the base revision), `request_digest`, `proposal_digest` (content fingerprint, checked
on every read), `status` (`pending` → `deciding` → `accepted` | `partially_accepted` |
`rejected`; `expired` is derived after 24 h), `claim`, `outcome`, `created_at`,
`expires_at`. Submission rules: only the draft's **own source Agent**, acting in the
**draft owner's live session** (session-origin permit), may propose; the base revision
must exist; the proposed result must be valid draft content and must change something;
the same key and body replay, a different body under a used key is
`idempotency.conflict`. Tenant, owner and proposer come only from the verified permit
(`ProposalAuthor.from_permit`); permits and session IDs are not stored.

**Decision** (`caos.agent_draft_proposal_decision` v1, `confirmed: true`):
`accept` names `fields` (a non-empty subset of the proposed fields) and the
`expected_revision` the human saw; `reject` declines the whole proposal. Every decision
re-runs the DRAFT-0.1 authorization (gateway human-to-source authority + creator policy)
and the owner/tenant/source/draft checks.

1. **Claim.** An acceptance whose `expected_revision` is not the current revision is
   refused before claiming (`draft.revision_conflict`), so a claim never names a future
   revision. Compare-and-set `pending` → `deciding` with the exact decision
   (`decision_digest` over proposal, decision, sorted fields, expected revision),
   `attempt = 1` and a 60 s lease. An identical request resumes or replays; after a final
   decision a different one is `proposal.already_decided`; a different one during the
   lease is `proposal.decision_in_progress`; after the lease see **Recovery** below.
2. **Evaluate (accept).** `expected_revision ≠ current` → `draft.revision_conflict`;
   a selected field whose value fingerprint differs from the base, or whose provenance
   names a revision after the base, → `proposal.field_conflict` with
   `conflicting_fields` and `applicable_fields`. Nothing is applied and the claim is
   released, so the human can accept only the applicable fields. No field is ever
   silently overwritten.
3. **Commit.** Draft revision `n + 1` through the DRAFT-0.1 `_id` CAS, with field
   provenance `{origin: proposal, revision, proposal_id}` for changed accepted fields and
   a digest-bound `cause {kind: proposal, proposal_id, decision_digest, accepted_fields}`.
   Accepting values the draft already holds commits nothing.
4. **Evidence, then finalize.** Seshat `agent.draft.proposal.accepted` /
   `agent.draft.proposal.rejected` (stable event ID per proposal), then compare-and-set
   to the final status with the outcome.

The revision insert is the single atomic gate. A retry, a concurrent identical request,
or any later decision first scans the revisions after the base for one caused by the
proposal and completes it (evidence + finalize) instead of applying again; a claim is
released only when its acceptance can no longer commit. Hence at most one accepted
revision per proposal, and a rejection can never follow a committed acceptance.
Concurrent acceptances of different proposals serialize on the revision CAS (the loser
gets `draft.revision_conflict` and may retry against the new revision, where field
conflicts are detected).

**Recovery (closeout).** A crashed decision blocks other decisions for at most its lease,
not the proposal's 24 h lifetime. After the lease, the next authorized request takes the
claim over by compare-and-set on `claim.attempt` (exactly one recoverer wins; the lease
is renewed) and completes the **claimed** decision, never its own: it runs exactly as
that decision's retry would. If the claimed acceptance can no longer commit (revision or
field conflict), it is released and the new decision proceeds; otherwise the new request
gets `proposal.already_decided`. Evidence is attributed to the human who made the claimed
decision (the claim's stored authorization).

*Why stale attempts cannot commit after takeover (equivalent atomic guarantee).* There is
still one commit point, the insert of draft revision `expected + 1` for the claim. A
stale original attempt and a recoverer execute the identical decision, so at most one of
them inserts it and the other recognizes the committed revision by its `cause` (twin).
A claim is released only by an attempt that saw it unable to commit: the draft was
already past `expected` (that revision exists and, if caused by this proposal, the
pre-evaluation scan would have found it) or the selected fields conflict at revision
`expected` (identical for every attempt). Because a claim's `expected` is never a future
revision, no attempt of a released claim can later insert `expected + 1`. A different
decision therefore starts only when the old one can no longer commit, and a stale
rejection cannot finalize after takeover (finalization is CAS on the claim). The
one-proposal-to-at-most-one-accepted-revision guarantee is unchanged.

**Seshat** (`audit/models.py`, `AgentDraftProposalDecisionMetadata`): `proposal_id`,
`draft_id`, `source_agent_id`, `proposer_agent_id`, `base_revision`,
`resulting_revision`, `accepted_fields`, `rejected_fields`, `proposal_digest`,
`decision_digest`, `content_digest`; `correlation_id = draft_id`,
`causation_id = proposal_id`. Attribution follows the G3.3 approval convention: the
**actor** is the deciding human (`actor.user_id`, `authorization.subject_id`) acting
through the draft gateway's executor (`actor.principal_id`, Genesis locally); the
**authority** is the one-way UTU provenance of that gateway authority (target
`data.validation@1.0.0`); the **proposer** is recorded separately
(`metadata.proposer_agent_id`, Enki); **causation** is the proposal. Never draft values,
rationale, prompts or credentials. Ordinary submission records no Seshat event (it
changes nothing); the proposal record keeps the proposer's UTU provenance.

**HTTP** (human only; there is deliberately **no submission route**):

| Method and path | Result |
| --- | --- |
| `GET /creation-drafts/{draft_id}/proposals/{proposal_id}` | Proposal with `state`, `current_revision`, `conflicting_fields`, `applicable_fields`, `outcome` |
| `POST /creation-drafts/{draft_id}/proposals/{proposal_id}/decision` | `{proposal, draft}` after the decision |

Errors: `404 proposal.not_found` (another owner's, tenant's, source's or draft's proposal
is indistinguishable), `409 proposal.field_conflict` (with field lists and
`current_revision`), `409 draft.revision_conflict`, `409 proposal.already_decided` /
`decision_in_progress` / `expired`, `422` malformed or `proposal.invalid` (fields not
proposed), `503 proposal.unavailable` / `draft.unavailable` (repeat the identical
request), `500 proposal.corrupt`.

**Verification (2026-10-30, DRAFT-0.2A).** `tests/agents/test_draft_proposals.py` (50 tests at DRAFT-0.2A, 57 after the closeout):
DRAFT-0.1 recovery (concurrent roll-forward repair, broken chain, unchanged revision
digests); submission attribution, isolation, idempotency and strict contracts; full,
selective, no-op and conflicting acceptance (including changed-and-restored values);
stale revisions; rejection; expiry; evidence outage after commit; write lost after
commit; claim interrupted before commit; 6 identical concurrent acceptances (one
revision, one event); 6 mixed concurrent decisions (one winner); concurrent acceptance
of different proposals; integrity of accepted revisions; no review/approval/creation
effect and unchanged G3.3 review; HTTP contract. `test_draft_proposals_integration.py`
(real Mongo + Redis, 3 consecutive runs): mixed concurrent HTTP decisions after a human
edit created a field conflict, exactly one winner and one `coll_logs` record, G3.3
review of the accepted draft, no change to Agents/approvals/creations/instances, replay
from a new session, non-creator denied. Full suite: 2489 passed, 9 skipped, the 5
inherited signing-key failures. The live `caos` database has no draft collections.

**Closeout verification (2026-10-30).** Recovery: crash before commit recovered after the
lease (claimed acceptance completed, attempt 2), crash before commit whose acceptance went
stale (released, new rejection wins), crash after commit, evidence outage, a stale
attempt resuming after takeover with its old view (no commit), a stale identical attempt
(harmless twin), 8 concurrent recoverers (one takeover, one revision, one event), future
`expected_revision` refused before claiming; attribution (human actor, executor
principal, proposer and causation recorded separately; no values). Capability: §9.

**Limitations.** No list-proposals endpoint yet (§10.1); recovery scans the revisions
after the base (proportional to edits since the proposal); authority is checked before
each commit (bounded revocation window, as for G3.4); a recovered decision is the one
originally claimed (a different intent waits for its release or completion); no
`superseded` status; proposals have no retention policy.

## 9. Enki proposal capability `agent.draft.propose@1.0.0` (approved, implemented)

Source: `agents/creation_drafts/capability.py`; registration in
`dependencies/genesis_bootstrap.py` only when the reviewed policy grants the signal.

| Item | Implemented contract |
| --- | --- |
| Signal | `agent.draft.propose@1.0.0`, provider `system.agent_drafts`, registered at bootstrap in the frozen `SynapticRegistry`; Nataraja declares and runtime-confirms it |
| Grant | Enki (`…0004`) only: `config/genesis.local.json` `invoke` gains `agent.draft.propose`; pinned in `genesis_local.py` (`_ENKI_PROPOSER_GRANTS`); new policy revision `3e2ea22f…`; **no trusted line** (none required: Enki calls the provider directly) |
| Scope | `PROPOSE_SCOPE = CapabilityScope("agent.draft.propose", PROPOSE_BINDING, {PROPOSE_BINDING})`. The provider requires `permit.scope == PROPOSE_SCOPE`, `binding_chain[-1] == PROPOSE_BINDING`, `entry_operation == "agent.draft.propose"`, `provider_version == "1.0.0"`, target/tenant/source equal to the permit, and `origin == "session"` |
| Caller | Trusted in-process `submit_proposal(authority, exchange, principal, submission)`: attenuates Enki's verified principal to the scope, binds, requests the exact version, revokes and drains. The generic `POST /agents/{id}/signals/{signal}` route carries an unscoped permit and is **denied** (tested); there is no client-facing submission route |
| Authority | `check_permit` (session live, not revoked or expired) before work and before the receipt; current principal re-resolved (policy versions unchanged) and root grant re-checked |
| Payload | `caos.agent_draft_proposal_submission` v1: `idempotency_key` + `proposal` (`AgentDraftProposalRequest`); strict, so identity, owner, status, approval or proposer fields are refused (`proposal.invalid_request`) |
| Constraints | `ProposalAuthor.from_permit`: tenant, requester and source Agent from the permit; the draft must be owned by that requester and have that Agent as source (`draft.not_found` otherwise) |
| Result | `caos.agent_draft_proposal_receipt`: `ok`, `proposal_id`, `draft_id`, `base_revision`, `state`, `created` (replay `false`); never draft content |
| Excluded | Enki cannot edit drafts, decide proposals, approve, create, activate or reach Store; Genesis and JOSHUA cannot attenuate to the scope (no root grant); Thoth and Nabu hold nothing new |

Verification: `test_draft_proposal_capability.py` (23 tests: scoped idempotent submission,
unscoped/over-wide scope/bootstrap/version/entry/target/source/tenant mismatches,
revoked/changed/ungranted authority, privileged or arbitrary fields, other Agent or
human, exact registration) and `test_draft_proposal_capability_integration.py` (reviewed
local policy, disposable Mongo/Redis: only Enki holds the grant and no trusted line;
Enki submits and replays through real UTU/CPBX; Genesis and JOSHUA cannot attenuate; the
generic signal route is denied; the human accepts with evidence attributing the human and
naming Enki as proposer; after logout Enki can no longer propose; no Agent, approval or
creation change). Policy pins updated in `test_genesis_initiation_integration.py` and
`test_recursive_genesis_integration.py` (Enki's exact grant set).

## 10. DRAFT-0.2B contracts (as documented before implementation)

Retained as the pre-implementation contract; §11 records what was implemented and where
it was refined (approval binding route, `approved` status, `open`/`decided` filters).
These preserve the authoritative G3.3 review digest and current G3.4 creation semantics.

### 10.1 Proposal listing

`GET /agents/{source}/creation-drafts/{draft_id}/proposals?state=pending|decided|all&limit=…&cursor=…`
(human only). Authorization exactly as other draft reads; the query is always
`{tenant_id, draft_id, owner_subject_id}` from the verified requester, never from the
query string. Order `created_at desc, proposal_id desc`; `limit` 1–50 (default 20);
`cursor` an opaque encoding of the last `(created_at, proposal_id)` and the filter, which
grants nothing (the server re-applies the owner filter). Items are the existing
`public()` projection with derived `state`; `expired` is a derived state. Requires a
non-unique compound index `(tenant_id, draft_id, created_at, _id)` on
`coll_agent_draft_proposals` created by an idempotent `ensure_indexes` (Cosmos
compatibility to verify, as for `coll_agents`).

### 10.2 G3.3 review adapter for an exact revision

`POST /agents/{source}/creation-drafts/{draft_id}/revisions/{revision}/review`: authorize
and load the owner's exact committed revision, build `AgentDraftValidationRequest` from
`revision.creation_draft()` and call the **unchanged** `AgentDraftValidationGateway.review`.
Response: the unchanged review envelope plus `draft_ref {draft_id, revision,
content_digest}`. The review snapshot and digest do not include the draft reference, so
the digest equals a body-based review of the same content (already tested).
Approval by reference: `ReviewApprovalRequest` gains an optional `draft_ref {draft_id,
revision}`; the server reads that revision (owner-scoped), requires it to be the draft's
current revision at approval time, uses its content as the draft (if a body is also
sent it must equal it), re-reviews and keeps all existing digest, idempotency, Seshat
and `effective()` rules. The approval record stores `draft_ref` outside the digest. G3.4
consumes the approval exactly as today.

### 10.3 Lifecycle: consumed, read-only, fork

- Status gains `consumed` and `abandoned`; each status change is a new revision with
  unchanged content and a `cause` (`{kind: creation, creation_id, approval_id, agent_id}`
  or `{kind: abandon}`), through the same revision CAS (retried at `current + 1`, since it
  never conflicts on content).
- Consumption is written after G3.4 reports `completed` for an approval with `draft_ref`
  (trusted creation completion, idempotent; reads also reconcile from the creation
  record). It never blocks, delays or invalidates a committed or in-flight creation;
  creation always uses the approval snapshot. Revisions committed after the approved
  revision remain in history; the consumed marker names the approved revision.
- A consumed or abandoned draft is read-only: `PATCH`, proposal decisions and new
  proposals return `409 draft.consumed` / `draft.abandoned`; pending proposals become
  undecidable.
- Fork: `POST /creation-drafts/{draft_id}/fork` (`Idempotency-Key`, chosen revision)
  creates a new draft whose revision 1 copies that content with `forked_from {draft_id,
  revision}` and fresh `user` provenance; the original is unchanged.

### 10.4 Enki form migration to server-authoritative drafts

The client form (`/v2/forms/[agentId]`) finds or creates the user's open draft for Enki
(needs `GET /agents/{source}/creation-drafts?status=editing` — requester-scoped,
paginated, index `(tenant_id, owner_subject_id, source_agent_id, updated_at)`), saves
through `PATCH` with `expected_revision`, reviews through §10.2, approves by reference
and creates through the unchanged G3.4 route. Pending proposals (§10.1) are shown with
`applicable_fields`/`conflicting_fields` and accepted per field. The browser no longer
holds the authoritative draft; Ptah tab navigation (ENKI-1.0 §0.4–§0.6) is PTAH-0.2.

### 10.5 Unsaved edits and revision conflicts (client representation)

- The client keeps `base = {revision, content}` (last server-confirmed) and `local`
  (unsaved); dirty fields are those where `local ≠ base`. The server never stores unsaved
  edits; the client may keep them in session storage keyed by `draft_id` and base
  revision.
- Save sends only dirty fields (`changes`/`remove`) with `expected_revision =
  base.revision`. On `409 draft.revision_conflict` the client fetches the current
  revision and rebases per field: non-dirty fields take server values; a dirty field the
  server did not change (server value equals base) keeps the local value; a dirty field
  the server also changed (by the human elsewhere or an accepted proposal — provenance
  shows which) is presented as a field conflict for explicit "mine/theirs" choice. Nothing
  is overwritten automatically; the retried `PATCH` uses the new revision.
- Review and approval apply only to a saved revision; the UI marks unsaved changes as not
  reviewed. Proposal acceptance requires the current saved revision (save or discard local
  edits first, or rebase as above).

## 11. DRAFT-0.2B: shared draft integration (implemented, uncommitted)

### 11.1 Backend

| Concern | Implementation |
| --- | --- |
| Draft listing | `GET /agents/{source}/creation-drafts?status=editing\|approved\|consumed\|open\|all&limit=1..50&cursor=` (`open` = editing or approved). Owner-scoped (`tenant`, `owner`, `source` from the verified requester only), newest first (`sort_key`, `_id`), each item rolled forward and reconciled. Index `draft_owner_recent (tenant_id, owner_subject_id, source_agent_id, sort_key desc, _id desc)` on `coll_agent_drafts` |
| Proposal listing | `GET …/creation-drafts/{draft_id}/proposals?state=open\|decided\|all&limit&cursor` (owner of the draft only; `open` includes expired-undecided proposals, shown as `expired`). Index `proposal_draft_recent (tenant_id, draft_id, owner_subject_id, sort_key desc, _id desc)` on `coll_agent_draft_proposals` |
| Cursors | Opaque base64url of the last `(sort_key, _id)` plus a digest of the query scope; a cursor from another query, or a tampered one, is `422 draft.invalid_request`; it grants nothing |
| Sort keys | Storage-only fixed-width UTC text (`YYYYMMDDTHHMMSS.ffffffZ`), because ISO strings of variable length do not sort correctly; never part of digests or projections |
| Indexes | Created idempotently on first listing (`create_index`). **Cosmos DB compatibility unverified** (local MongoDB only) |
| Exact-revision review | `POST …/creation-drafts/{draft_id}/revisions/{revision}/review`: the server reads the owner's committed revision and calls the **unchanged** `AgentDraftValidationGateway.validate`; response = unchanged review envelope + `draft_ref {draft_id, revision, content_digest}`. The digest algorithm is untouched; equal content ⇒ equal digest |
| Approval binding | `POST …/creation-drafts/{draft_id}/approval` (`caos.agent_draft_approval`: `revision`, `review_digest`, `decision: approve`, `confirmed: true`, `idempotency_key`). The revision must be current and `editing`; the server builds the body from that revision and calls the **unchanged** G3.3 `DraftReviewApprovalService.approve` (current authority, dependency fingerprints, digest match, idempotency, Seshat). Then a content-unchanged `approved` revision (cause `approval`: `approval_id`, `review_digest`, `reviewed_revision`) freezes the draft through the revision CAS. If the draft moved meanwhile, the just-made approval is revoked with `require_unconsumed` and the human gets `409 draft.revision_conflict`. Replaying the same key returns the frozen state |
| Freeze | `approved` and `consumed` drafts refuse `PATCH`, proposal acceptance (including a recovered claimed acceptance, which is released) and Enki submissions with `409 draft.approved` / `draft.consumed` (+ `current_revision`); rejection of proposals stays possible |
| Reopen | `POST …/reopen` (`caos.agent_draft_reopen`, `expected_revision`). An unconsumed approval is revoked atomically (G3.3 `revoke(require_unconsumed=True)`: the revocation CAS also requires `consumed_by` empty, else `ApprovalConsumed`); an approval consumed by a **failed** creation is spent; an accepted or provisioning creation refuses (`409 draft.creation_in_progress`); a completed one is recorded as consumption. Then a content-unchanged `editing` revision (cause `reopen`). A new review and approval are required |
| Consumption | On every draft read and listing, an `approved` draft whose approval was consumed by a G3.4 creation record in state `completed` gets one content-unchanged `consumed` revision (cause `consumption`: `approval_id`, `review_digest`, `reviewed_revision`, `creation_id`, `agent_id`), idempotent under concurrency (revision CAS). Anything short of `completed` changes nothing, so no lifecycle transition can affect a committed or reconciling creation. The content digest of the reviewed content is retained |
| Fork | `POST …/fork` (`caos.agent_draft_fork`, `revision`, `Idempotency-Key`): a new `editing` draft copying one of the requester's own revisions, with `forked_from {draft_id, revision}` carried on every revision; same authority as creating a draft; the original is unchanged |
| Statuses | `DraftStatus = editing \| approved \| consumed`; typed `RevisionCause` kinds `proposal \| approval \| reopen \| consumption` with per-kind fields (absent fields omitted, so earlier digests are unchanged) |
| Evidence | No Seshat events for edits or lifecycle revisions. Existing events cover consequential steps: G3.3 approval and revocation (including reopen withdrawals), G3.4 creation, proposal decisions |
| Wiring | `AgentDraftLifecycleService` (`agents/creation_drafts/lifecycle.py`) at `app.state.agent_draft_lifecycle`, composed from the unchanged G3.3 approval service and the G3.4 creation repository. No grant, signal, trusted line or policy change |

The one G3.3 code change is the opt-in `require_unconsumed` flag on
`DraftReviewApprovalService.revoke` (default behavior unchanged).

### 11.2 Enki form (`caos-client`)

- `/v2/forms/{agentId}` renders `DraftWorkspace` with `persistence="server"`; the
  local-only mode (G3.2 behavior and tests) remains for other uses and as the fallback
  when server drafts are unavailable (shown as an alert; edits stay in the page).
- Same-origin proxy `src/shared/auth/lib/agent-drafts.ts` behind
  `src/app/api/caos/v2/agents/[agentId]/drafts/[[...path]]/route.ts`: a fixed allowlist
  of method/path shapes and query parameters, UUID-checked IDs, same-origin for writes,
  required `Idempotency-Key` for create and fork, server-held bearer, and only typed error
  fields (`code`, `retryable`, `current_revision`, `conflicting_fields`,
  `applicable_fields`) crossing the boundary.
- `useServerDraft` creates or resumes the newest `open` draft (one creation key per Agent
  in `localStorage`, so concurrent tabs create one draft), keeps unsaved edits in this
  tab's `sessionStorage` across reloads, saves dirty fields with `expected_revision`,
  refreshes on focus and every 15 s, and rebases unsaved edits per field (§10.5): server
  values for untouched fields, local values for locally edited fields, explicit
  "Keep mine / Use saved value" conflicts when both changed. Invalid interface JSON is
  never sent (shown as not saved).
- Pending proposals (`DraftProposalsPanel`): per-field checkboxes, conflicting fields
  disabled, "Accept selected" (requires no unsaved edits) and "Reject"; accepted values
  appear in the form.
- Review and approval (`useCreationReview` server mode): validate reviews the saved
  revision (never unsaved edits), approve binds that revision and freezes the form; the
  existing Create Agent action is unchanged (`approval_id` from the draft's lifecycle, so
  it survives reloads); after completion the form shows the consumed, read-only draft
  with "Start a new draft from this one". Revoke is hidden for server drafts (reopen is
  the governed path). No tabs (PTAH-0.2); the conversation is not connected.

### 11.3 Verification (2026-10-30)

- Backend `tests/agents/test_draft_lifecycle.py` (15): owner-scoped paginated listing,
  cursor tamper and cross-query refusal, limit bounds, index creation; proposal listing
  and filters; exact-revision review digest equality; approval binding and freeze
  (PATCH, Enki submission and second approval refused; replay); approval with a stale
  revision or digest; an edit racing the approval (approval revoked with evidence, 409);
  concurrent edit vs approval (one consistent outcome); reopen with an unconsumed
  approval (revoked), with an in-progress creation (refused), after a failed creation
  (allowed); atomic unconsumed revocation; confirmed consumption once under 5 concurrent
  readers, content digest retained, read-only afterwards; fork isolation; expired
  approval keeps the draft frozen until reopened; no grants, events or collections beyond
  drafts.
- Backend `tests/agents/test_draft_lifecycle_integration.py` (2; disposable Mongo + Redis,
  3 runs): two sessions (tabs) racing saves (one 200, one 409), listing resume, review
  digest equal to a body review, approval freeze, unchanged G3.4 creation of one sleeping
  Agent, consumption, consumed reopen refused, no grant/approval/activation change, fork
  and paginated listing on real MongoDB with the index present; reopen withdrawing an
  unconsumed approval (G3.4 then refuses it, revocation evidenced); non-creator denied on
  every new route.
- Existing suites updated for the new route (`test_creation_drafts.py`,
  `test_draft_proposals.py` HTTP fixtures; the submission-route test now allows the GET
  listing and still forbids any write).
- Client: `agent-drafts.test.ts` (4: allowlist mapping, refusals, typed conflict details,
  201 and size bound), `serverDraft.test.ts` (4: round trip, dirty/storable payload,
  rebase, authority claims refused), `DraftWorkspace.server.test.tsx` (7: resume and
  reload with unsaved edits, single creation, multi-tab merge and explicit conflict, use
  saved value, proposal discovery and selective acceptance, review/approve/freeze/create/
  consumed, unavailable fallback). Existing form/review tests unchanged and passing.
- Full backend suite with integrations: 2537 passed, 9 skipped, 5 inherited signing-key
  failures. Full client suite: 1365 passed, the same 33 inherited failures in 8 unrelated
  files. Ruff, Prettier and ESLint clean for changed files; mypy only the inherited Motor
  stub warning; `tsc` reports nothing in changed files.
- Live: no policy change (revision `3e2ea22f…`), Nataraja passed with no findings, 3
  Agents, no draft collections in `caos`, no leftover test databases.

### 11.4 Known limitations

- Cosmos DB compatibility of the two listing indexes is unverified.
- Consumption is recorded on read (draft GET, listing, form refresh after creation), not
  pushed by G3.4; until a read, an approved draft whose creation completed still shows
  `approved`.
- An approval made through the legacy body-based G3.3 route is not bound to any draft.
- If the process stops after a G3.3 approval but before the freeze revision, the draft
  stays `editing` with an effective approval of its current content; repeating the same
  approval completes the freeze, while editing leaves that approval usable only for the
  content it approved (as before DRAFT-0.2B).
- An expired approval leaves the draft frozen until the human reopens it.
- Unsaved edits are per tab (`sessionStorage`); a draft resumed in a new tab starts from
  the server state. Unsaved edits to a draft that became frozen are discarded on refresh.
- The form polls (15 s and on focus); there is no push channel.
- Revision history and proposals have no retention policy.
