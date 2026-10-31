# Governed Activity Context (AGENT-CTX-0.1)

**Status:** implemented and accepted (2026-10-31), uncommitted (`feature/ops-v2` over
`b70ab0b`); CONVERSATION-0.1A follow-up corrections (§3, §4, §9) await review. Supersedes
ENKI-CTX-0.1. Decision basis:
[ADR-0004](architecture/decisions/ADR-0004-conversation-tool-and-activity-context.md).
**Audience:** engineers building the conversation coordinator (ENKI-1.0-B), Ptah
interactions (PTAH-0.1a) and future activity owners.

Shared infrastructure that links an *activity* (for example a creation interview) to
independently owned resources by typed references. It is not a signal, Tool or route,
it grants nothing and it knows nothing about what an activity means. Agent identity is
data (the actor's principal), never a namespace.

## 1. Components (`src/domains/agentverse/activities/`)

| Module | Responsibility |
| --- | --- |
| `models.py` | `caos.activity_context` v1 record, `caos.activity_scope_head` v1, typed value objects, errors |
| `registry.py` | `ActivityContractRegistry` (exact `id@version`, fingerprinted, frozen) and `ResourceResolverRegistry` (one resolver per resource kind, frozen); `ResourceSlot`, `SlotLink`, `ResolvedResource` |
| `owners.py` | Owner kinds. `conversation.v2`: UTU conversation membership and Agent access, the Agent's existing `chat.response` turn grant, and the CONV-REC-0.1 turn lease as the owner attempt |
| `repository.py` | `coll_activity_contexts`, `coll_activity_context_heads` |
| `service.py` | `ActivityContextService`, `ActivityActor`, `view()` requester projection, `ActivityRefresh` |

Domain-owned registrations (wired in `genesis_bootstrap` as `app.state.activity_contexts`):

| Kind or contract | Owner domain | Resolution |
| --- | --- | --- |
| `manuscript.session` | `manuscripts/activity.py` | `ManuscriptService.owned`. Reports revision, status, pinned `id@version` and fingerprint, restart predecessor and attached draft |
| `agent.creation_draft` | `agents/creation_drafts/activity.py` | MANUSCRIPT-0.1C `DraftReferenceReader` (tenant, owner, source Agent) |
| `agent.draft.proposal` | same | Proposal repository read. The proposal must be for the same tenant, human and source Agent; reports its draft link |
| `agent.creation_interview@1.0.0` | same | Contract: `manuscript_session` (required, `successor`, start `active`, update `active` or `completed`), `draft` (required, `immutable`, `editing`), `proposal` (optional, `replace`). Links: session → draft, proposal → draft |

## 2. Contracts

**Record (`caos.activity_context` v1).**
- identity and scope: `context_id`, `tenant_id`, `scope_key`;
- `actor {subject_id, principal_id, principal_kind: "agent"}` (System Citizens are not
  admitted yet);
- `owner {kind, id}`;
- `activity {key, fingerprint}`;
- lifecycle: `generation`, `status` (`active` / `closed`), `closed_reason`,
  `previous_context_id`;
- `references {slot: {kind, id, observed_revision}}`, validated against the pinned
  contract;
- `revision` and `applied` (the last 32 operation IDs with digests);
- immutable `origin`, `start_digest`, `started_references`;
- causation `created_by` / `last_owner {event_id, fence, operation_id}`;
- `pending_evidence` (at most 4);
- timestamps and `state_digest`.

**Scope head (`caos.activity_scope_head` v1, `_id = scope_key`).**
- `generation`, `active_context_id`, `revision`, `state_digest`;
- optional `intent {operation_id, digest, successor (the full record), close_previous}`.

Never stored: permits, credentials, messages, model output, answers or resource
content. Requester projection (`view()`): identity, activity, owner, generation, status,
revision and references only.

## 3. Authority

- **Actor:** `ActivityActor.from_principal` accepts only a verified session-origin
  permit whose requester matches the permit's human. Bootstrap is refused. The actor
  names no capability: evidence provenance is derived per owner kind from the owner's own
  `audit_signal` (CONVERSATION-0.1A).
- **Owner check, on every call:** for `conversation.v2`, UTU `authorize_conversation`
  (membership, tenant, access to the Agent) plus the Agent's existing `chat.response`
  grant. That grant is specific to the `conversation.v2` adapter; other owner kinds
  declare their own (the generic test owner requires none). A missing context, other tenant, other human, other Agent, lost membership or
  missing grant is the same `activity.not_found`.
- **References:** resolved read-only by their owning domain's resolver, before every
  binding change.
  - `activity.reference_unavailable`: missing or not the actor's (indistinguishable).
  - `activity.reference_state`: an owned resource in a disallowed state.
  - `activity.reference_inconsistent`: cross-slot links don't match.
  - `activity.reference_not_successor`: an invalid successor move.
- **No authority is added:** contract or resolver registration and context storage
  confer no authority to execute Manuscript, draft or other operations. The service holds
  only frozen, read-only resolvers.

## 4. Lifecycle and concurrency

| Operation | Effect |
| --- | --- |
| `active`, `history`, `get`, `refresh` | Read-only |
| `current` | Read-only. The active generation plus `materialized` (its document is stored and active) and `reconciliation_pending` (a head intent or undelivered evidence remains). A decided successor whose document is missing is reported with `materialized: false`; reads never complete it |
| `start` | Requires no active generation. One head compare-and-set decides generation N+1 and records the activation intent; then the successor is inserted and the intent cleared |
| `switch` | Requires the head's active generation at `expected_revision`. One head compare-and-set decides N+1 and records the intent to close N; then N is closed (`switched`), N+1 inserted, intent cleared |
| `advance` | Re-observes every reference; `replace` / `successor` slot updates only |
| `close` | Context compare-and-set to `closed` with its evidence obligation, then the head's active pointer is released |
| `reconcile` | Authorized, bounded scope recovery: completes a decided intent, releases a stale pointer, delivers pending evidence |

**Guarantees.**
- **At most one active generation per scope:** the head is a single document keyed by
  `_id`, its active pointer changes only by compare-and-set, and an activation is decided
  in that same write.
- **No orphaned switches:** a concurrent `start` cannot interleave, because it requires
  no active generation.
- **Crash recovery:** an interrupted `start` or `switch` (for example after closing N but
  before inserting N+1) is completed exactly as decided, by replaying the operation or by
  any authorized `reconcile`. No competing successor can be decided meanwhile.
- **Idempotency:** a repeated identical operation returns the recorded result; a reused
  operation ID with a different payload is `activity.operation_conflict`; a stale
  revision is `activity.revision_conflict`.
- **Owner fencing:** writes require the owner attempt to be live and not older than the
  last writer (`activity.stale_owner`). This is logical idempotency plus a pre-write
  ownership check, **not** Redis/Mongo atomicity. A lease can expire between the check
  and the write; the next writer's compare-and-set and fence comparison then reject
  stale state.

## 5. Seshat evidence (outbox)

- **Events:** `activity.context.started`, `activity.context.switched` and
  `activity.context.closed`, with subject `activity_context`, target and authorization
  provenance from the owner kind (`chat.response@1.0.0` for `conversation.v2`) and
  correlation owner → owner-attempt event.
- **Metadata:** references (slot, kind, ID, revision), revisions, fingerprints,
  generation, predecessor and closure reason only.
- **Atomic obligation:** the obligation is written in the same document write as its
  transition (the started/switched obligation is inside the head intent's successor;
  the closed obligation is in the close compare-and-set).
- **Delivery:** looks up the deterministic event ID first, appends if absent, treats a
  concurrent conflict as recorded, then settles the obligation with a same-revision
  compare-and-set.
- **Recovery:** if Seshat or the process fails, the obligation stays pending and is
  discoverable (`MongoActivityContextRepository.with_pending_evidence`). Any later write
  or `reconcile` on the scope delivers it, without client retries, without duplicates,
  and without a new broker, background worker or grant.

## 6. Storage compatibility

- **Primitives:** only `_id` uniqueness, single-document `insert_one` / `replace_one` with
  a filter (compare-and-set), `find_one` by `_id`, and `$in` on `_id`. There is no
  secondary unique or partial index, no multi-document transaction, and no sort.
- **Mongo vs Cosmos:** these are the same primitives the draft, review and audit
  repositories rely on for MongoDB / Cosmos DB Mongo API parity. Verified on MongoDB 8.0
  (disposable). **Cosmos DB for MongoDB vCore was not tested.**
- **Inspection query:** `with_pending_evidence` filters on `pending_evidence.0` without
  an index; it is a bounded scan meant for operator inspection.

## 7. Verification (2026-10-31, disposable Mongo and Redis)

`activities/tests/`:
- `test_activity_contracts.py` (15, no database);
- `test_activity_generic.py` (8, real Mongo and Seshat): a second, distinct contract for
  another ordinary Agent with test resolvers and owner;
- `test_creation_interview_integration.py` (7): the ported ENKI-CTX-0.1 tests on real UTU,
  Enki, V2 conversation, turn leases, Manuscript sessions, drafts, a real proposal and
  Seshat.

Plus the audit catalog samples. Full backend: 2963 passed, 11 skipped, 5 inherited
root-signing failures. CONVERSATION-0.1A added owner-specific provenance checks (the
generic owner's evidence carries its own signal and its actor holds no `chat.response`)
and `current()` checks of an unresolved successor intent versus a materialized
generation.

## 8. Conversation Tool boundary (CONVERSATION-0.1A)

- A `conversation@1.0.0` attachment may declare exact activity contract keys
  ([Conversation Tool](CONVERSATION_TOOL.md) §2). They resolve against this frozen
  registry, and the contract must allow the `conversation.v2` owner kind.
- This package does not import or depend on the Conversation Tool or its resolver, and a
  declaration grants no access to a context.

## 8A. Definition consent for new generations (REV-5)

- **Owner consent:** `start` and `switch` (a new generation) require the owner kind's
  explicit `permits_activity` consent (part of `OwnerResolver`). MIG-FIX-0.1: an owner
  kind without it never consents (fail closed).
- **`conversation.v2`:** consults the Agent's current definition through an injected
  callable that returns an explicit set; an unwired callable, or any answer that is not
  a set, denies:
  - explicit Conversation Tool: only its declared `activities`;
  - reviewed legacy compatibility: only the entry's reviewed `activities` (Enki: the
    creation interview; Thoth and Nabu: none). A contract registered later is never
    implied;
  - declared contracts that do not resolve in the bound registry for `conversation.v2`
    are dropped; no composition or unbound contracts: none.
- **A removed contract** therefore blocks new generations, while existing contexts stay
  readable (ownership still applies) and can be advanced, closed and reconciled.
  Replaying a recorded start returns its recorded context.
- **No retained authority:** consent grants nothing; the owner's grant check still runs
  on every call.
- **Dependency direction:** the package still never imports the Conversation Tool.

## 9. Not included

No Ptah runtime (PTAH-0.1a), coordinator
(ENKI-1.0-B), Workflow/operation owner kind, System Citizen actors or Nataraja reporting
of pending evidence (the repository query exists).
