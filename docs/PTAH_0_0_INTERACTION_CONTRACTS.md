# PTAH-0.0 — Interaction, Conversation-Context and Turn-Recovery Contracts

**Status:** contract design for architectural review (2026-10-31, `feature/ops-v2` @ `b70ab0b`;
the document and its implemented parts are committed in `c1719b9`).
> **Correction (2026-10-31, [ADR-0004](architecture/decisions/ADR-0004-conversation-tool-and-activity-context.md), approved):**
> - §4's Enki-specific context is replaced by the generic Governed Activity Context
>   (AGENT-CTX-0.1, implemented);
> - the optional `conversation@1.0.0` Tool is registered and drives conversation
>   composition (CONVERSATION-0.1A, implemented and accepted;
>   [Conversation Tool](CONVERSATION_TOOL.md));
> - the approved generic interaction identity is **`conversation.submit@1.0.0`**,
>   replacing `enki.conversation.submit@1.0.0` throughout this document (§2.4). It is a
>   design: no Interaction Registry, Ptah runtime or interaction frame exists.

**Implemented (committed in `c1719b9`):** §3 turn recovery (CONV-REC-0.1, accepted), §6
draft-reference integrity (MANUSCRIPT-0.1C, accepted), and conversation-linked context
storage as the generic [Activity Context](ACTIVITY_CONTEXT.md) (AGENT-CTX-0.1, accepted;
it supersedes §4's ENKI-CTX-0.1, which was never committed). The interaction envelope (§2), the Enki turn coordinator
and the Ptah runtime remain designs. It defines the prerequisites for ENKI-1.0-B.
**Audience:** CAOS architects and the engineers who will implement the milestones in §8.
**Decision basis:** the architect's approved decisions 1–10 (§0),
[Ptah](PTAH_REALM_ARCHITECTURE.md), [ENKI-1.0](ENKI_1_0_ARCHITECTURE.md),
[Manuscripts](MANUSCRIPTS.md), [Agent Drafts](AGENT_DRAFTS.md),
[REASONING-0.1 §13–§14](REASONING_0_1_ARCHITECTURE.md), [Catalog](CATALOG.md).

Labels: **implemented** (verified in source), **approved** (architect decision),
**proposed** (this document; requires review).

## 0. Approved decisions

1. Independent resources connected by durable explicit references (Model D).
2. Authoritative Enki conversation-context links live in MongoDB, independent of the Redis
   conversation transport.
3. A context is created only when a creation interview actually starts.
4. CAS revisions, idempotency and ownership checks are preserved.
5. Explicit Agent Tool configuration is preferred; live Enki is not modified by this task.
6. The initial 60-second turn deadline is kept and propagated across all steps.
7. Crashed-turn recovery is a prerequisite, not accepted technical debt.
8. A supplied draft reference is validated at Manuscript session start and revalidated before
   consequential operations.
9. `enki.conversation.submit@1.0.0` and `draft.proposal.decide@1.0.0` are registered Ptah
   interaction contracts.
10. Results are typed references and outcomes, never executable client instructions.

## 1. Current implementation (source evidence)

| Concern | Implemented behavior | Evidence |
| --- | --- | --- |
| Transport | Ticket (HTTP, 30 s, single use) → one authenticated V2 socket per session (15 s renewable Redis lease); ≤ 16 KiB frames; ≤ 8 in-flight events; per-socket turn lock | `interfaces/api/v2/communication.py` |
| Client events | One type: `ClientEventV2{conversation_id, event_id, type: "chat.message", response_mode: context\|llm, agent_ids, message}` (`extra="forbid"`); plus `event.cancel` | `entities/conversation_v2.py` |
| Routing | Server-fixed signals (`chat.response` / `chat.context`); fresh UTU per target; Khepri awaken; 60 s (llm) / 10 s budget | `services/client_event_dispatcher_v2.py` |
| Replies | Terminal only: `chat.response`/`chat.context{results}`, `event.error{code}`, `event.cancelled`, `session.ready`. **No progress frames.** `chat.response` results include `llm.{key, provider, model}` | dispatcher, `services/cognitive_response_v2.py` |
| Conversations | Redis, no TTL: immutable `ConversationV2{id, tenant_id, member_user_ids, agent_ids, visibility}` (no metadata), per-user event journal (digest → replay/pending/conflict, ≤ 256), turn key, transcript | `repositories/conversation_v2.py` |
| Turn admission | `SET :turn identity` (no TTL, no fence, no lease); `FINISH` checks `:turn == identity` | same |
| Crash behavior | **A crashed in-flight turn leaves `:turn` forever: the conversation answers `busy` permanently.** No recovery code exists despite the module docstring | same |
| GUI declarations | Agent `gui_components` v1: `chat` / `form` (Enki: `agent_configuration` form over `caos.agent_creation_draft@1`, default; `conversation` chat). Presentation only | `entities/gui_components.py`; live Enki (read-only) |
| Manuscript sessions | Mongo, ID = uuid5(tenant, subject, agent, idempotency key); `draft_id` optional, **never validated**; revision CAS; replay window 32 | `manuscripts/service.py`, `sessions.py` |
| Drafts | Human-created over HTTP; ID = derived from (tenant, subject, source Agent, key); revision CAS; statuses editing/approved/consumed; Enki only proposes (`agent.draft.propose`) | `agents/creation_drafts/` |
| Proposal decision | Human HTTP route; `ProposalDecisionRequest` (strict, `confirmed: true`); decision digest; leased claim (60 s) with CAS takeover | `creation_drafts/proposals.py`, `proposal_service.py` |
| Enki composition | Persisted `tools: []`; Role `enki` `default_tools: []`, `required_tools: {}`; grants include `manuscript.session.*`, `catalog.project`, `cognitive.interpret`, `agent.draft.propose`, `chat.response` | live Enki (read-only), `agents/roles/enki/spec.py`, `config/genesis.local.json` |
| Ptah | **No Ptah code.** §5 envelope in the Ptah architecture is illustrative | `src/` (no Ptah module) |

## 2. Ptah interaction contracts (proposed; approved interaction keys)

### 2.1 Envelope — `caos.ptah.interaction` v1

A new, additive client frame on the existing authenticated V2 socket (ticket handshake,
socket lease and turn admission unchanged). `chat.message` stays valid and unchanged.

```json
{
  "type": "interaction.submit",
  "contract": "caos.ptah.interaction",
  "schema_version": 1,
  "interaction": "enki.conversation.submit@1.0.0",
  "event_id": "client-generated-idempotency-id",
  "target": {"agent_id": "…", "component_key": "conversation"},
  "conversation_id": "…",
  "input": {}
}
```

- Strict (`extra="forbid"`), ≤ 16 KiB frame, `event_id` follows `Identifier`.
- **No** tenant, user, permit, signal, provider, version-of-a-binding, handler, URL, model,
  prompt, scope or grant field exists. Identity comes only from the authenticated session.
- `conversation_id` is required by conversation-scoped interactions and forbidden otherwise.
- `interaction` is an exact `key@MAJOR.MINOR.PATCH`; no ranges, no `latest`.

### 2.2 Canonical binding identity

A server-private, frozen **Interaction Registry** (Ptah §3.3) maps each exact
`interaction@version` to a `PtahInteractionBinding`:

| Field | Meaning |
| --- | --- |
| `key`, `version` | Exact identity (the registry key) |
| `input_model` | Strict Pydantic contract; the only accepted `input` |
| `applies_to` | Component types and form sources it may target (validated against the target Agent's persisted `gui_components`) |
| `scope` | `conversation` or `resource` (decides `conversation_id` presence) |
| `handler` | In-process trusted service call; **never serialized, never client-selectable** |
| `result_contract` | Typed projection contract name |
| `authority` | Which existing authority the handler re-establishes (documentary; enforcement stays in UTU/CPBX/services) |

Registration and discovery grant nothing. Unknown key or version →
`interaction.unsupported`; a target component that does not exist on the Agent, or does not
satisfy `applies_to` → `interaction.not_applicable`. Both are typed `event.error` codes,
indistinguishable from an unreadable Agent where applicable.

### 2.3 Server pipeline (every interaction)

1. Re-establish the session from the socket's verified ticket session; re-check it is live.
2. Resolve the exact binding; reject unknown/unsupported.
3. Load the target Agent definition; require READ access for the requester
   (`can_access_resource`), the target component, and `applies_to`.
4. Validate `input` against `input_model` (strict).
5. For conversation scope: `authorize_conversation(session, conversation, (agent_id,))`.
6. Transport admission (§3): journal digest replay / pending / conflict; turn lease.
7. Run the handler with a trusted absolute deadline (§3.5). Every consequential step runs
   its own existing UTU/CPBX or service authorization.
8. Re-authorize the conversation and session before releasing output.
9. Return the typed projection (§2.6); commit journal + transcript under the turn fence.

### 2.4 `enki.conversation.submit@1.0.0` → `conversation.submit@1.0.0`

> **Generalized (ADR-0004, CONVERSATION-0.1A):** the identity is
> `conversation.submit@1.0.0`, and it is not Enki-specific.
> - **Binding:** it applies to a `chat` component declared in an Agent's effective GUI
>   components, and resolves only if:
>   - the Agent's conversation composition is available and allows the requested mode (an
>     attached `conversation@1.0.0` Tool, or a temporary compatibility entry);
>   - the Agent's existing `chat.context` / `chat.response` grant authorizes the turn.
> - **Activity:** the activity it may run is one of the Tool's declared `activities`
>   (Enki's creation interview is `agent.creation_interview@1.0.0`, declared explicitly
>   after ENKI-COMP-0.1).
> - **Input contract:** `caos.enki.conversation_submit` is renamed in PTAH-0.1a, without
>   semantic change.
>
> The text below keeps the original Enki example.

- `applies_to`: Enki's `chat` component; scope `conversation`.
- `input` (`caos.enki.conversation_submit` v1):
  `{message: str[1..8192], draft_ref?: {draft_id, revision}}`.
  `draft_ref` names the draft the human currently has open; it is a **hint**, validated
  server-side (§6) and never an authority. It may start an interview only when no active
  context exists (§4.4).
- Authority (existing only): the human session and conversation membership; Enki's
  session-origin principal; `chat.response`; per step the scoped
  `manuscript.session.*`, `catalog.project`, `cognitive.interpret`, `agent.draft.propose`
  grants. No new grant, signal or trusted line.
- Handler: the ENKI-1.0-B turn coordinator (not specified here beyond its contract):
  status → interpret → answer → propose → reply, bounded by the turn deadline.

### 2.5 `draft.proposal.decide@1.0.0`

- `applies_to`: Enki's `form` component over `caos.agent_creation_draft@1`, and its `chat`
  component; scope `resource` (no `conversation_id`).
- `input`: `{draft_id, proposal_id}` + the existing `ProposalDecisionRequest` fields
  (`contract`, `schema_version`, `decision`, `confirmed: true`, `expected_revision`,
  `fields`).
- Handler: the existing `AgentDraftProposalService.decide` under the **human** session
  (unchanged claim lease, decision digest, revision CAS, Seshat evidence). Enki gains
  nothing; the existing HTTP route stays as a legacy bridge.

### 2.6 Response projection — `caos.ptah.interaction_result` v1

```json
{
  "type": "interaction.result",
  "contract": "caos.ptah.interaction_result",
  "schema_version": 1,
  "event_id": "server-id",
  "correlation_id": "client event_id",
  "interaction": "enki.conversation.submit@1.0.0",
  "conversation_id": "…",
  "outcome": "replied",
  "result": {}
}
```

`enki.conversation.submit` result (`caos.enki.turn_result` v1):

| Field | Type | Notes |
| --- | --- | --- |
| `outcome` | `replied` \| `needs_clarification` \| `proposal_ready` \| `interview_completed` \| `failed` | |
| `reply` | `{message_id, content}` \| null | Soul-expressed text; the only free text |
| `context` | `{context_id, revision}` \| null | §4 |
| `manuscript` | `{session_id, revision, stage, status, completed, total}` \| null | Progress only; no answers, guidance or provenance |
| `proposal` | `{proposal_id, draft_id, base_revision, fields}` \| null | References; the client reads details through existing draft/proposal reads |
| `failure` | `{code, retryable}` \| null | Typed codes only |

`draft.proposal.decide` result (`caos.draft.proposal_decision_result` v1):
`{outcome: accepted | rejected | conflict | expired | unavailable, draft: {draft_id, revision},
proposal: {proposal_id, status}}`.

Never present: URLs, signals, providers, handler names, scripts, machine definitions,
permits, model keys/providers, prompts, Reasoning output or Manuscript answer values.

### 2.7 Progress (proposed, optional)

`interaction.progress{correlation_id, step: manuscript | interpret | answer | propose | reply,
state: started | completed}`. Informational, not persisted, not replayed, carries no content;
clients must tolerate absence. Deferred unless approved.

### 2.8 Compatibility strategy

- `ClientEventV2` (`chat.message`) and its replies remain unchanged for all Agents.
- `interaction.submit` is an additive frame; a client that does not send it sees no change.
- Interaction keys are exact-version; a new major is a new registry entry. Removal requires a
  deprecation window documented in the registry.
- The typed V2 reply is a **new frame type** (`interaction.result`), not a change to
  `chat.response`. Journal replay stores and returns it verbatim.
- Transcript (`ConversationMessageV2`) keeps text. **Proposed (decision):** an additive,
  optional `refs` on `agent_response` messages (`context_id`, `context_revision`,
  `manuscript_session_id`, `proposal_id`) so reloaded history can re-resolve state; refs are
  re-authorized on read, never trusted.
- Finding (not changed here): existing `chat.response` replies disclose `llm.key`,
  `provider` and `model` to the client; the new projection omits them.

## 3. Turn recovery (CONV-REC-0.1: implemented, accepted, committed in `c1719b9`)

Implemented in `repositories/conversation_v2.py` and `services/client_event_dispatcher_v2.py`;
verified with disposable Redis/Mongo (`tests/communication/test_turn_recovery_v2.py`).
Differences from the original design below: the turn record is a JSON string with a Redis
TTL, not a hash. The fence counter and the last admitted identity live in a separate
`:turn:fence` hash in the same hash slot. A replay of the abandoned event itself also
reconciles it. The deadline value (§3.5) is **not** yet propagated: turns still run under
the dispatcher's existing `asyncio.timeout` (60 s llm / 10 s context), which the lease
ceiling covers.

### 3.1 Defects in the previous admission (fixed)

1. `:turn` has no TTL or lease: a crash blocks the conversation forever (`busy`).
2. No fencing token: `FINISH` compares `:turn` with the `(user, event_id)` identity only, so
   a stale worker of the same identity could commit after any takeover.
3. An in-flight journal entry without a response answers `pending` forever.

### 3.2 Turn record — `caos.communication.turn` v2

`:turn` (JSON string, `PX` TTL = lease) holds `{identity, fence, owner, ceiling}`; `fence`
comes from `HINCRBY :turn:fence seq` (monotonic per conversation), which also records the
last admitted identity. Times use Redis server `TIME` inside Lua; process deadlines use
`time.monotonic()` (§3.5). The journal entry records `{digest, request, fence, state:
running | settled, response?}`.

### 3.3 Operations (all atomic Lua)

| Operation | Semantics |
| --- | --- |
| `BEGIN` | Replay/pending/conflict as today for the same identity. If `:turn` is live (lease unexpired) → `busy`. If `:turn` exists but its lease expired → **reconcile** it (below) then admit. Admit: new fence, lease = now + 15 s, ceiling = now + 60 s + 15 s grace |
| `RENEW` | Only by `(identity, fence, owner)`; extends lease by 15 s, never past `ceiling_at`. Heartbeat every 5 s while executing |
| `FINISH` | Requires `(identity, fence)`; appends transcript, stores the terminal response, sets `state: settled`, deletes `:turn`. A stale fence returns 0 → the worker discards its output |
| `RECONCILE` | For an expired turn: settle its journal entry with terminal `event.error{code: "interrupted"}` (fence recorded), delete `:turn`. Never re-executes anything |

### 3.4 Invariants

1. At most one live turn per conversation (lease + fence).
2. Every admitted turn reaches exactly one immutable terminal response:
   completed, failed, cancelled or interrupted.
3. Replays of the same `(user, event_id)` with the same digest return that response; a
   different digest is `conflict`. Transport replay never re-executes a handler.
4. A stale attempt cannot commit journal, transcript or Mongo context state: Redis checks
   the fence; the context repository requires the context revision and rejects a lower
   turn fence (§4.3).
5. Leases are bounded by `ceiling_at`; no indefinite renewal.
6. Recovery never re-runs model inference or a consequential operation. Retrying is an
   explicit new human submission with a new `event_id`.
7. Consequential operations are idempotent by keys derived from
   `(context_id, event_id, step)` and guarded by resource CAS (Manuscript revision, draft
   `base_revision`, proposal claim). A new turn re-reads current revisions; it never
   replays an earlier turn's operation.
8. Recovery and replay re-run UTU conversation authorization before emitting anything.

### 3.5 Deadline propagation (decision 6; not yet implemented)

The dispatcher computes `deadline = time.monotonic() + 60` at admission and passes it
in-process (explicit parameter; the Reasoning context variable is set only by
`interpret()`). Every step runs under `min(step budget, deadline − now)`. The coordinator
reserves ≥ 15 s for the reply; if the remaining budget cannot fit an interpretation, it
skips it and asks a clarifying question. The Redis lease ceiling (60 s + 15 s grace) is
always later than the deadline, so a live worker is never fenced before its deadline.
No request field can set or extend a deadline.

### 3.6 Cross-service integration contract (for MANUSCRIPT-0.1C, ENKI-CTX-0.1, ENKI-1.0-B)

Turn fencing protects only the Redis journal and transcript. There is **no atomicity
between Redis and Mongo**, and lease expiry is not proof that an attempt's side effects
did not happen. A paused or partitioned worker may still complete a Mongo write after
its lease is lost. Therefore every consequential step that a turn performs must be safe on
its own:

1. **Deterministic idempotency keys.** Derive each operation's key from
   `(context_id, event_id, step)`, for example `sha256("enki-turn" | context_id | event_id |
   "manuscript.answer")` truncated to the target contract's key format. A stale and a live
   attempt of the *same* event therefore converge on one operation (Manuscript
   `request_key` replay window; draft-proposal idempotency key). A new event (a retry)
   gets new keys and must re-read current revisions.
2. **Revision CAS.** Use the target's own CAS (Manuscript `expected_revision`, draft
   `base_revision`, proposal claim lease). A stale attempt operating on an older revision
   is rejected by the owning service, not by the turn fence.
3. **Receipt reconciliation.** After an `interrupted` result, the client and the next turn
   reconcile from the authoritative resources: the conversation context (§4) records
   only references, and each referenced resource is re-read through its owning service.
   The turn coordinator must treat "receipt unknown" as "re-read state", never as "repeat
   the operation".
4. **Context writes** (ENKI-CTX-0.1) carry the turn fence and are rejected when it is
   lower than the stored one (§4.3). This narrows stale writes but, without cross-store
   transactions, does not make Redis and Mongo commit together.
5. **No retries of consequential work by recovery.** Recovery settles transport state
   only.

## 4. Enki conversation context (ENKI-CTX-0.1: superseded by AGENT-CTX-0.1, never committed)

### 4.0 Superseded: ENKI-CTX-0.1 (`agents/enki_context/`)

> **Superseded and removed (2026-10-31)** by AGENT-CTX-0.1, the generic
> [Governed Activity Context](ACTIVITY_CONTEXT.md) with the registered
> `agent.creation_interview@1.0.0` contract. The historical text below describes the removed
> package.
>
> **Under correction (historical):** this Enki-specific package, collection, `app.state` attribute and
> its `enki.context.*` events violate the rule that ordinary Agents own no core
> infrastructure. [ADR-0004 §5](architecture/decisions/ADR-0004-conversation-tool-and-activity-context.md)
> maps each one to a generic replacement. It also records two limitations found in
> review: a concurrent `start` can orphan a `switch`, and evidence is missing after a
> crash until the operation is repeated. Do not commit this package as is.

Storage, service and evidence exist. No coordinator calls them yet, and no route, signal,
frame or grant exposes them. `EnkiContextService` is constructed at startup as
`app.state.enki_contexts`; nothing is read or written at startup.

**Record.** `caos.enki.conversation_context` v1, one Mongo document per generation, with
`_id = context_id` (uuid5 of tenant, conversation, owner, Agent and generation). Fields:
- scope: tenant, owner, source Agent, conversation;
- `generation`, `status` (`active` / `closed`) and `closed_reason` (`completed`,
  `switched`, `abandoned`, `reset`), plus `previous_context_id`;
- references: `manuscript {session_id, id@version, fingerprint, observed_revision}`,
  `draft {draft_id, observed_revision}`, optional `last_proposal {proposal_id,
  base_revision}`;
- concurrency: CAS `revision`, `applied` (the last 32 operation IDs with digests);
- immutable creation facts: `origin` (`start` / `switch`), `start_digest`,
  `started_session_id`, `started_draft_revision`;
- turn correlation: `created_by` and `last_turn {event_id, fence, operation_id}`;
- timestamps and `state_digest`.

It never stores permits, messages, answers, draft content or model output.

**Indexes.** A unique `(tenant, conversation, owner, Agent, generation)` index, created on
first insert. At most one generation is active by construction: generation N+1 is inserted
under a deterministic `_id` only after generation N is closed, and closure is terminal.

**Actor and authorization.**
- The actor comes only from Enki's verified session-origin permit
  (`ContextActor.from_principal`, which also requires the existing `chat.response` turn
  grant). Bootstrap permits are refused.
- Every call re-checks UTU conversation membership and Enki access (`authorize_conversation`).
  A lost membership, foreign tenant, other human or other Agent is the same
  `context.not_found`.
- Manuscript sessions are re-resolved with `ManuscriptService.owned`, and drafts with the
  MANUSCRIPT-0.1C reader, which requires an `editing` draft.

**Operations.**
- `active`, `history`, `get` and `refresh` are read-only.
- `start` creates the next generation when an interview starts, from a Manuscript session
  with an attached draft.
- `switch` closes the active generation as `switched`, then starts the next one. A crash
  between the two writes is completed by repeating the operation.
- `advance` updates observed revisions, follows a restart successor that keeps the same
  draft and exact Manuscript, or records a proposal reference.
- `close` closes the generation.
- Writes are idempotent per caller-derived operation ID: an identical repeat returns the
  recorded result, a reused ID with different input is `context.operation_conflict`, and
  stale revisions are `context.revision_conflict`.

**Turn fencing (Redis → Mongo).**
- Writes require the calling turn's own CONV-REC lease. It must belong to this
  conversation and human, still be live (`ConversationRepositoryV2.owns`, read-only), and
  have a fence no lower than the context's `last_turn.fence`.
- These are checks, not atomicity: a lease can expire between the check and the Mongo
  write. Revision CAS and fence monotonicity then reject the stale writer's subsequent
  writes, and any writer that read old state.
- Recovery never re-runs operations.

**Seshat.**
- `enki.context.started`, `enki.context.switched` and `enki.context.closed` are recorded
  with subject `conversation_context`, target `chat.response@1.0.0` and correlation
  conversation → turn event.
- Metadata holds only references, revisions, fingerprints, generation and closure reason.
  Started and switched evidence is rebuilt only from the immutable creation facts.
- The context commits first. A failed audit write reports `context.unavailable`
  (retryable). Repeating the same operation ID finds the committed transition and only
  appends the missing record: it checks the deterministic event ID first and treats a
  concurrent conflict as already recorded. Advances and reads are not audited.

The sections below are the original design.


### 4.1 Record — `caos.enki.conversation_context` v1 (`coll_enki_conversation_contexts`)

| Field | Notes |
| --- | --- |
| `contract`, `schema_version` | `caos.enki.conversation_context`, 1 |
| `context_id` | uuid5 of `(tenant_id, conversation_id, owner_subject_id, source_agent_id, ordinal)` |
| `tenant_id`, `owner_subject_id` | From the verified session; never a request body |
| `source_agent_id` | Enki's Agent ID from the session-origin permit |
| `conversation_id` | Redis conversation reference |
| `ordinal` | 1, 2, … per (tenant, conversation, owner, agent); a new interview after a closed one increments it |
| `status` | `active` \| `completed` \| `closed` \| `abandoned` |
| `manuscript` | `{session_id, manuscript: id@version, fingerprint, observed_revision}` |
| `draft` | `{draft_id, observed_revision}` |
| `last_proposal` | `{proposal_id, base_revision}` \| null |
| `revision` | CAS integer, starts at 1 |
| `causation` | `{started_by_event_id, last_event_id, last_turn_fence}` |
| `created_at`, `updated_at`, `state_digest` | |

Never stored: permits, tokens, credentials, messages, prompts, model output, Manuscript answer
values, draft content, proposal content.

### 4.2 Indexes

Unique `(tenant_id, conversation_id, owner_subject_id, source_agent_id, ordinal)`; partial
unique `(tenant_id, conversation_id, owner_subject_id, source_agent_id)` where
`status = "active"` (at most one active context).

### 4.3 Repository semantics

- `start(...)`: insert-only and idempotent: the same deterministic ID and start digest
  replays; a different digest conflicts.
- `get_active(tenant, conversation, owner, agent)`: owner-checked; foreign or missing are
  indistinguishable.
- `advance(previous, following)`: compare-and-set on `revision`, and rejects
  `causation.last_turn_fence` lower than the stored one (stale worker).
- No delete; terminal statuses only.

### 4.4 Lifecycle

1. No context exists for ordinary conversation turns.
2. An interview starts on a submit carrying `draft_ref` when no active context exists:
   validate the draft (§6) → start the Manuscript session with an idempotency key derived
   from the would-be `context_id` → insert the context. A crash between steps converges:
   re-derivation finds the same session.
3. Each turn reads the active context, then **independently** resolves each reference through
   its owning service under the current principal (`ManuscriptService.owned`,
   `AgentDraftService.owned`); the context grants nothing.
4. `completed` when the Manuscript session completes; `closed` when its draft becomes
   approved or consumed, or the human starts over; `abandoned` when a referenced resource
   becomes unavailable (typed `context.reference_unavailable`, human action required).

## 5. Enki composition audit (decision 5)

- **Implemented:** `manuscript@1.0.0` and `reasoning@1.0.0` are registered first-class Tools;
  the Enki grants exist.
- **Missing:** live Enki has `tools: []`; the Enki Role declares no default or required
  Tools; the pinned Genesis creation entry (`config/genesis.local.json` `bootstrap[enki]`)
  has no `tools`. Bootstrap verification does not compare `tools`.
- **No governed revision path for an existing Agent exists.** The Store is insert-only; the
  V1 `PATCH /api/v1/agents/id/{id}` validates tools but bypasses G3.3 review, human
  approval and Seshat. It must not be used for a governed system Agent.

**Smallest governed approach (proposed, ENKI-COMP-0.1):**

1. Add explicit references to Enki's pinned Genesis creation entry, validated by the
   existing creation pipeline for new environments:
   `{"key": "manuscript", "config": {"default_manuscript": "agent_creation_interview@1.0.0"}}`,
   `{"key": "reasoning", "config": {"tasks": ["manuscript.answer_extraction@1.0.0"]}}`.
2. For the existing live Enki: a governed **definition revision** (draft → G3.3 review →
   explicit human approval of the exact digest → a Store revision handoff with revision CAS
   and Seshat evidence). This is a new authority path and requires its own approval; it is
   not designed here.
3. Until then, the coordinator **fails closed** (`enki.tools_unconfigured`) when Enki lacks
   explicit references. No implicit default, silent attachment or grant.

## 6. Manuscript draft-reference integrity (decision 8; MANUSCRIPT-0.1C implemented, accepted, committed in `c1719b9`)

**As implemented** ([Manuscripts §9A](MANUSCRIPTS.md)), with these differences from the
design below:
- The port method is `reference(...)`.
- An idempotent start replay checks only that the draft is still the actor's, not its
  status, because history is never rewritten.
- `complete` and a new `restart` successor require `editing`. Replays of recorded
  restarts and completions are not re-checked.

Design text:

- A narrow read port in the Manuscript domain, `DraftReferenceReader.verify(tenant_id,
  subject_id, source_agent_id, draft_id) -> {draft_id, revision, status}`, implemented by a
  read-only adapter over `AgentDraftService.owned` ownership rules (tenant, owner subject,
  source Agent). It exposes no content and no write method.
- `ManuscriptService.start` with a `draft_id`: the reference must exist, be owned by the
  actor (`actor.subject_id`, `actor.agent_id`) and be `editing`. Missing or foreign →
  `session.draft_unavailable` (indistinguishable); owned but not editing →
  `session.draft_not_editable`. Validation runs before insert and again on idempotent replay.
- Unchanged: the session schema (`draft_id` already exists), start digest (already includes
  `draft_id`), session ID derivation, fingerprints, pinning and replay window.
- Revalidated before consequential operations: Manuscript `restart` (carries `draft_id`) and
  `complete`; the coordinator before `agent.draft.propose` (which keeps its own ownership and
  `base_revision` CAS).
- No draft-write authority is added to Manuscript.

## 7. Authority implications

| Contract | New authority? |
| --- | --- |
| Interaction envelope, registry, typed result | None. Presentation and routing only; bindings are server-private |
| `enki.conversation.submit@1.0.0` | None. Existing human session, conversation membership, `chat.response` and Enki's scoped grants |
| `draft.proposal.decide@1.0.0` | None. Existing human decision path |
| Turn recovery | None. Transport integrity |
| Conversation context | None. A trusted in-process repository used by the coordinator; references only |
| Draft-reference validation | None. Read-only port |
| Enki Tool references | Live revision path **requires a separate approval** (§5) |

The coordinator needs a CPBX exchange injected into the `chat.response` provider so it can
call the existing trusted callers (`interpret`, `invoke_manuscript`, `project_catalog`,
`submit_proposal`). This is wiring, not a grant. Each step attenuates its own single-signal
scope from Enki's root principal, so CPBX does not link those calls as children of the
`chat.response` request; the turn's `event_id` is used as their correlation and causation ID.

## 8. Implementation milestones (proposed order)

| Milestone | Scope | Authority |
| --- | --- | --- |
| **CONV-REC-0.1** | Turn record v2: lease, fence, renew, reconcile, `interrupted`; dispatcher heartbeat (**implemented, accepted, committed in `c1719b9`**; the deadline value moves to ENKI-1.0-B) | None |
| **MANUSCRIPT-0.1C** | `DraftReferenceReader` + start/restart/complete validation (**implemented, accepted, committed in `c1719b9`**) | None |
| **ENKI-CTX-0.1** | Context model, Mongo repository, indexes, CAS + fence (**superseded by AGENT-CTX-0.1**, [Activity Context](ACTIVITY_CONTEXT.md)) | None |
| **PTAH-0.1a** | Minimal frozen Interaction Registry, `interaction.submit` frame, the two interactions, typed results, optional transcript `refs` | None |
| **ENKI-COMP-0.1** | Pinned Genesis entry references; governed revision path for live Enki | Revision path needs approval |
| **ENKI-1.0-B** | Turn coordinator using all of the above under the 60 s deadline | None beyond existing grants |

Each milestone: disposable infrastructure, no live writes, the paused development API kept
paused unless separately authorized.

## 9. Open decisions

1. Progress frames (§2.7): include in PTAH-0.1a or defer.
2. Transcript `refs` (§2.8): approve the additive field.
3. The governed definition-revision path for live Enki (§5.2).
4. Whether context creation/closure is a consequential Seshat event.
5. Whether to remove `llm` details from existing `chat.response` replies (§2.8 finding).
