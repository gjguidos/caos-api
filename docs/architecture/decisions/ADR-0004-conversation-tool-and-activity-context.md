# ADR-0004 — Conversation Tool and Governed Activity Context

**Status:** **Approved** as the architectural direction, with the conditions in §10
(2026-10-31). **Implemented (uncommitted, awaiting review):**
- AGENT-CTX-0.1, accepted ([Activity Context](../../ACTIVITY_CONTEXT.md));
- CONVERSATION-0.1A: Tool registration and registry-driven composition, accepted
  ([Conversation Tool](../../CONVERSATION_TOOL.md), §11);
- AGENT-DEF-SEC-0.1: governed-definition integrity and identity-bound compatibility
  (§12, [Agent Definition Governance](../../AGENT_DEFINITION_GOVERNANCE.md)).

Governed definition revision (ENKI-COMP-0.1), the Ptah runtime and the coordinator remain
proposals.

**Date:** 2026-10-31

**Scope:** Correct two AOSA inconsistencies before PTAH-0.1a and ENKI-1.0-B:
1. Ordinary-Agent infrastructure in the core: ENKI-CTX-0.1's `agents/enki_context/`.
2. Conversation exists only as implicit shared infrastructure, not as an optional,
   Agent-configurable Tool.

**Evidence base:** source at `b70ab0b` on `feature/ops-v2` plus the uncommitted
CONV-REC-0.1, MANUSCRIPT-0.1C, ENKI-CTX-0.1 and PTAH-0.0 work. The live Agent records were
read once, read-only, during PTAH-0.0. Related: [ADR-0003](ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md),
[PTAH-0.0 contracts](../../PTAH_0_0_INTERACTION_CONTRACTS.md),
[ENKI-1.0](../../ENKI_1_0_ARCHITECTURE.md),
[REASONING-0.1 §14](../../REASONING_0_1_ARCHITECTURE.md), [Manuscripts](../../MANUSCRIPTS.md).

## 1. Five distinct concepts

| Concept | What it is | Authority | CAOS examples |
| --- | --- | --- | --- |
| **Tool** | A registered, optional, configurable resource an Agent may have attached (`ToolSpec` + `AgentToolRef`). Describes behavior and exposes capability actions declaratively | None. Registration, discovery, attachment and configuration never authorize | `manuscript@1.0.0`, `reasoning@1.0.0`, proposed `conversation@1.0.0` |
| **Capability** | A versioned `signal@version` operation bound to a provider in `SynapticRegistry` and executed through CPBX | UTU permit + CPBX grant or trusted line, per principal and tenant | `chat.context@1.0.0`, `chat.response@1.0.0`, `manuscript.session.*`, `cognitive.interpret` |
| **Shared infrastructure** | Trusted runtime services and stores every Agent uses through the same contracts | Owns its own integrity (identity, CAS, leases); grants nothing | V2 communication runtime (ticket, socket, `ConversationRepositoryV2`, journal, CONV-REC leases), Seshat, OPS V2, the proposed Activity Context Service |
| **System Citizen** | A privileged, policy-registered principal with no Agent document, performing operating-system functions | Its own reviewed grants and trusted lines | JOSHUA, Genesis |
| **Ordinary Agent** | A persisted `Agent` composed from type → Role → Soul → RulePacks → Tools, with UTU grants per principal | Its grants only | Enki, Thoth, Nabu |

**Rule:** an ordinary Agent never owns a core module, repository, collection, `app.state`
attribute, schema or Seshat event type. Its specific behavior is composition: registered,
versioned contracts selected by configuration and executed by shared infrastructure.

**Why:** dedicated infrastructure turns configuration into code. It makes the Agent
non-reproducible by Genesis, invisible to registry-driven validation and catalogs, and
privileged by implementation rather than by grants. It also breaks recursivity: Enki
must be creatable by Enki.

## 2. Current architecture (source evidence)

### 2.1 How chat behavior reaches an Agent today

| Path | Mechanism | Evidence |
| --- | --- | --- |
| V1 | `chat@1.0.0` ("Chat Synthesis Tool") is a **core** Tool auto-attached to every V1 Agent (`CORE_TOOL_KEYS = ("chat", "datetime", "cta")`, `_ensure_core_tools`); invoker route | `tools/chat_tool.py`, `agents/factory.py` |
| V2 context | `chat_v2@2.0.0` ("Chat Context Tool V2") is registered with `exposure` unset, `ownership: system` and `chat.context` declared `access: core`. Its handler `chat_context` is subscribed unconditionally at import (provider `tool.chat`) | `tools/chat_tool_v2/__init__.py`, `synaptic.py` |
| V2 response | `chat.response` provider `cognitive.chat` (`CognitiveResponseV2`) is subscribed at bootstrap if any grant holds it. It is not tied to any Tool | `dependencies/genesis_bootstrap.py`, `services/cognitive_response_v2.py` |
| Gate | `supports_v2_conversation(type, role)` = `type == "chat" or (type, role) == ("form", "enki")`. A **hardcoded Agent/Role identity check**, used by the response handler, `data.validation`, the Genesis composer and the GUI validator | `agents/conversational_composition_v2.py` and its four callers |
| Authority | Per-principal grants: Enki and Thoth hold `chat.context` and `chat.response`; **Nabu holds no grants** | `config/genesis.local.json` |
| Presentation | GUI v1 `chat` component (`ChatComponent`); presentation only | `entities/gui_components.py` |
| Transport | Ticket → one V2 socket per session; `ClientEventV2{chat.message, response_mode}`; dispatcher calls the fixed signal; Redis journal, CONV-REC leases and fencing | `interfaces/api/v2/communication.py`, `services/client_event_dispatcher_v2.py`, `repositories/conversation_v2.py` |

**Conclusion.** Conversation already exists as two *implicit* Tool registrations (V1
`chat`, V2 `chat_v2`) and two capabilities. Its availability to an Agent is decided by a
hardcoded type/role function, not by attachment or configuration. Persisted V2 Agents
attach no conversation Tool: live Enki, Thoth and Nabu all have `tools: []`.

### 2.2 The ENKI-CTX-0.1 specialization (uncommitted)

| Item | Enki-specific artifact |
| --- | --- |
| Package | `src/domains/agentverse/agents/enki_context/` (`models.py`, `repository.py`, `service.py`, `tests/`) |
| Classes | `EnkiConversationContext`, `MongoEnkiContextRepository`, `EnkiContextService`, `ContextActor`, `ContextRefresh`, `Context*` errors |
| Contract and storage | `caos.enki.conversation_context` v1; `coll_enki_conversation_contexts`; index `enki_context_scope_generation` |
| Schema coupling | Fixed `manuscript`, `draft`, `last_proposal` fields: the creation interview hardwired into the model |
| Bootstrap | `di_container.py` `enki_context_repository` (+ alias); `genesis_bootstrap.py` `app.state.enki_contexts` |
| Seshat | `enki.context.started` / `switched` / `closed`, `EnkiConversationContextMetadata` (the `conversation_context` subject type is generic and can stay) |
| Tests | `agents/enki_context/tests/` (15); samples in `tests/audit/test_caos_log.py` |
| Docs | PTAH-0.0 §4/§4.0, ENKI-1.0 notes, Implementation Status, Handoff |

Generic and kept: `ConversationRepositoryV2.owns(lease)`, CONV-REC-0.1, and
MANUSCRIPT-0.1C's `DraftReferenceReader` and Manuscript draft validation.

## 3. Proposed: `conversation@1.0.0` (first-class, optional Tool)

### 3.1 Identity and declaration

`ToolSpec`, following the `reasoning@1.0.0` pattern:

| Field | Value |
| --- | --- |
| Identity | key `conversation`, version `1.0.0` |
| Classification | `tool_class: conversational`, `scope: agent`, `exposure: public`, `ownership: global`, `execution_mode: synaptic_only` |
| Optional | Not in `CORE_TOOL_KEYS`, never auto-attached |
| Declared actions | Existing `chat.context@1.0.0` (provider `tool.chat`) and `chat.response@1.0.0` (provider `cognitive.chat`), both `access: granted` with no trusted lines. Declarative only |
| V1 | `execute` and `_run` always raise; no route |

**No new capability signal is required.** The Tool groups the two existing capabilities
for a conversational Agent. `chat_v2@2.0.0` remains the internal context-provider
implementation; its catalog exposure should become `internal` in CONVERSATION-0.1A. The V1
`chat` core Tool is unchanged for V1 Agents.

### 3.2 Configuration (`ConversationToolConfig`, strict, `extra="forbid"`)

| Field | Meaning | Constraints |
| --- | --- | --- |
| `modes` | Conversational modes this Agent offers: `context` (`chat.context`), `response` (`chat.response`) | 1–2 unique literals; narrows only |
| `history_window` | Transcript messages projected per turn | 0–50, default 12 (today's `ChatToolV2Config` default) |
| `activities` | Exact `key@version` activity contracts (§4) this Agent may run inside a conversation | 0–8 unique registered keys; narrows only |

Configuration cannot express models, providers, prompts, grants, scopes, tenants,
permits, turn deadlines or handler names. Execution still requires the per-principal
grants.

### 3.3 Catalog metadata

The safe RG-02F / `caos.catalog` projection exposes:
- identity, description and tags;
- a `modes` multiselect;
- an `activities` multiselect whose options carry each activity's title and description,
  using the `ToolConfigField.options` added in REASONING-0.1C.

It never exposes providers, signals, handler paths or activity internals.

### 3.4 Gate change (replaces the identity check)

`supports_v2_conversation(type, role)` is replaced by a registry-driven predicate,
`conversation_profile(definition)`:
- the Agent's persisted `AgentToolRef(key="conversation")`, validated;
- composed with its type's and Role's structural compatibility, declared in their specs
  rather than hardcoded.

The dispatcher, response handler, `data.validation` and the Genesis composer all use it.
Authority is unchanged: grants are still checked on every turn.

### 3.5 Ptah and non-conversational Agents

- A GUI `chat` component is presentation. `data.validation` adds a coherence rule: a
  `chat` component requires an attached `conversation` Tool with at least one mode.
- PTAH-0.0's `enki.conversation.submit@1.0.0` is generalized to
  **`conversation.submit@1.0.0`**. It applies to a `chat` component of an Agent whose
  conversation profile is valid; the handler is the generic coordinator, which runs the
  Agent's configured activity contract (§4).
- An Agent without the Tool is non-conversational:
  - V2 conversation creation still checks only READ access;
  - turns addressed to it return the existing `composition_unavailable`;
  - it remains usable through Workflows and forms.
- Nothing is granted automatically.

### 3.6 Backward compatibility

| Agent | Today | Transition | After ENKI-COMP-0.1 |
| --- | --- | --- | --- |
| Existing V2 Agents of type `chat`, plus Enki | Converse via the hardcoded gate | Compatibility profile: an Agent passing the old predicate and lacking the Tool is treated as `conversation@1.0.0` with default config (modes from its grants: `context`, plus `response` if granted). Nataraja reports it as `implicit_conversation` | Explicit `AgentToolRef` through a governed revision; compatibility profile removed |
| New Agents (Genesis, G3.4) | Same gate | `data.validation` requires the explicit Tool for a `chat` component | Same |
| Nabu | `chat` type, no grants | Profile valid, but every turn is still denied (no grant) | Unchanged authority |

## 4. Proposed: AGENT-CTX-0.1 — Governed Activity Context

### 4.1 Package and ownership

- **Location:** `src/domains/agentverse/activities/`. It's a domain service: it uses UTU,
  AgentVerse resources and Seshat, so it can't live in `src/base`. It's also not an Agent
  subpackage, because it serves Agents and System Citizens alike.
  (`src/domains/agentverse/context/` already holds V1 connection context, so reusing that
  name would be confusing.)
- **Persistence:** one shared collection, `coll_activity_contexts`.
- **Registries** (exact keys, frozen at bootstrap, like the Manuscript and Reasoning
  registries):
  - an `ActivityContractRegistry`;
  - a `ResourceResolverRegistry`.

### 4.2 Contracts

**Activity contract** (`caos.activity.contract` v1, fingerprinted, registered by the
domain that owns its semantics):

| Field | Meaning |
| --- | --- |
| `key@version` | e.g. `agent.creation_interview@1.0.0` (registered by the drafts/Manuscript domain, not "Enki") |
| `owner_kinds` | Which owning resources may anchor it (initially `conversation.v2`) |
| `references_model` | A strict Pydantic model of typed slots: each slot names a resource kind, whether it is required, whether it is immutable within a generation, and its update rule (e.g. "may follow a Manuscript restart successor") |
| `start_requirements` | Per-slot resolver predicates (e.g. draft `editing`, session `active` with the same draft) |
| `evidence` | Which transitions are consequential (default: started, switched, closed) |

**Resource resolver** (one per kind, owned by that resource's domain). It runs
`resolve(actor, ref) -> ResolvedRef{kind, id, revision, status}` through the owning
service: `conversation.v2` via UTU `authorize_conversation`, `manuscript.session` via
`ManuscriptService.owned`, `agent.creation_draft` via `DraftReferenceReader`, and
`agent.draft.proposal` via the proposal service's owned read. Missing and not-yours are
indistinguishable. Resolvers return no content.

**Context record** (`caos.activity_context` v1):
- `context_id`, deterministic from scope and generation;
- `tenant_id`;
- `actor`: `subject_id` (the human), `principal_id`, and `principal_kind`
  (`agent` | `system_citizen`);
- `activity`: `key`, `version`, `fingerprint`;
- `owner`: `kind`, `id` (a V2 conversation now; an OPS V2 operation later);
- `generation`, `status` (`active` | `closed`), `closed_reason`, `previous_context_id`, and a
  `successor` reservation (§6.1);
- `references`: the contract's typed model, serialized;
- `revision`, `applied` operation IDs;
- immutable creation facts: `origin`, `start_digest`, `started_references`;
- `owner_fence` and `last_owner_fence`: `{kind: conversation_turn | operation_lease, token,
  event_id}`;
- `pending_evidence` (§6.2);
- timestamps and `state_digest`.

**Seshat:** generic `activity.context.started`, `activity.context.switched` and
`activity.context.closed`. Metadata: context ID, activity key, version and fingerprint,
owner kind and ID, principal, generation, revision, `(slot, kind, id, revision)` tuples,
predecessor and closure reason. These replace the three `enki.context.*` events.

### 4.3 Service semantics (kept from ENKI-CTX-0.1)

- Actor from a verified session-origin permit, or a System Citizen's reviewed authority.
- Fresh owner-resource access on every call.
- References re-resolved before any binding change.
- Writes: `start` / `switch` / `advance` / `close` with CAS and operation-ID idempotency;
  reads are side-effect free; closed history is readable.
- Owner fencing: a conversation-turn owner uses the CONV-REC-0.1 lease (`owns` + fence);
  a future `operation.v2` owner uses OPS V2 ownership fencing tokens.

The service never knows what an activity means; it validates against the registered
contract.

### 4.4 Future non-conversation owners

A Workflow execution would register the owner kind `operation.v2`, with a resolver over
OPS V2 operations and fencing through `src/base/operations/v2/ownership.py`. No such
integration is built now; the model only leaves room for it (`owner.kind`,
`owner_fence.kind`).

## 5. Enki specialization → generic replacement

| ENKI-CTX-0.1 | Replacement |
| --- | --- |
| `agents/enki_context/` | `activities/` (`models.py`, `registry.py`, `resolvers.py`, `repository.py`, `service.py`) |
| `EnkiConversationContext` with fixed manuscript/draft/proposal fields | `ActivityContext` plus the `agent.creation_interview@1.0.0` contract's references model `{manuscript_session (required, follows restart successor), draft (required, immutable, editing at start), proposal (optional)}` |
| `caos.enki.conversation_context`, `coll_enki_conversation_contexts` | `caos.activity_context`, `coll_activity_contexts` |
| `MongoEnkiContextRepository`, DI `enki_context_repository` | `MongoActivityContextRepository`, DI `activity_context_repository` |
| `app.state.enki_contexts` | `app.state.activity_contexts` (a shared service) |
| `enki.context.*` events and metadata | `activity.context.*` and `ActivityContextMetadata` |
| `ContextActor` (Enki-named in docs only) | `ActivityActor` (agent or system citizen) |
| Hardcoded `chat.response` turn grant | The owner kind's resolver requirement (a conversation-turn owner requires the Agent's existing conversational grant through its conversation profile) |

**Migration:** none in a live database. The collection was never written outside disposable
test databases, and the uncommitted ENKI-CTX-0.1 code is replaced before commit. The 15
tests are re-expressed against the generic service and the registered
`agent.creation_interview@1.0.0` contract, with every invariant kept.

## 6. Concurrency and audit corrections (testable)

### 6.1 One active generation (limitation 1)

**Finding.** At most one active document holds today, but only by service protocol:
- The `(scope, generation)` unique index duplicates `_id` uniqueness.
- A concurrent `start` can land between `switch`'s close and its successor insert:
  1. `switch` closes N as `switched`.
  2. `start` sees N closed and inserts N+1 with its own session.
  3. `switch`'s insert collides.
- The result is a switch that closed N with no successor of its own, and a replay of it
  then fails forever with `context.active_exists`.

**Correction.**
1. **Storage-enforced uniqueness:** a partial unique index on the scope fields with
   `partialFilterExpression: {status: "active"}`. Any writer, including a bug, is refused.
2. **Successor reservation:** `switch` closes N **and records `successor: {operation_id,
   generation: N+1}` in the same single-document CAS**. `start` must refuse when the latest
   closed generation carries a reservation from another operation. A replay of the switch
   completes its own reserved successor. Reservations are never reassigned.
3. **Tests:**
   - an injected pause between close and insert, with a concurrent `start` → no orphan;
   - two concurrent switches → exactly one successor;
   - a direct second active insert is rejected by the index;
   - replaying an interrupted switch converges.

### 6.2 Missing Seshat evidence after a crash (limitation 2)

**Finding.** The transition commits first, and evidence is appended afterwards. If the
process dies between the two, the evidence is missing until the same operation is
repeated, and nothing guarantees that repeat.

**Correction (transactional outbox in the same document):**
1. Every consequential transition writes its evidence descriptors (deterministic
   `stable_event_id`, immutable metadata) into `pending_evidence` **in the same
   single-document CAS** as the transition.
2. After commit, the service appends each descriptor to Seshat (look up first, append if
   absent, treat a conflict as recorded), then clears it with a CAS.
3. **Reconciliation without background infrastructure:**
   - any later read or write on the same scope drains pending evidence first;
   - Nataraja `inspect` reports contexts whose evidence has been pending longer than a
     threshold (read-only).
4. **Tests:**
   - a crash after commit → the next access appends exactly one record;
   - concurrent drains → one record;
   - a drain after the context has advanced still records the original transition's
     metadata.

### 6.3 Logical idempotency vs execution fencing

- **Operation-ID replay (logical idempotency)** guarantees one transition per operation ID.
- **Owner fencing (execution fencing)** rejects writes from a turn or operation that no
  longer owns its owner resource, or that is older than the last writer.

Fencing is a pre-write check, not a cross-store transaction: a lease can expire between
the check and the Mongo write. Downstream consequential operations (Manuscript, draft
proposal) remain responsible for their own idempotency and CAS (PTAH-0.0 §3.6).
CONV-REC-0.1 is unchanged.

## 7. Authority and compatibility review

| Area | Change | New authority? |
| --- | --- | --- |
| Tool Registry | Register `conversation@1.0.0`; set `chat_v2` exposure to internal | No |
| Agent composition | Replace `supports_v2_conversation` with the registry-driven conversation profile (plus a temporary compatibility profile) | No |
| UTU/CPBX | None. Same `chat.context` / `chat.response` / scoped grants; no trusted line | No |
| Conversation routing | Dispatcher gate uses the profile; transport, journal and leases unchanged | No |
| Context ownership | Generic actor and owner resolvers, still UTU-checked | No |
| Manuscript and Draft | Unchanged; resolvers reuse `owned` and `DraftReferenceReader` | No |
| Ptah bindings | `conversation.submit@1.0.0` replaces `enki.conversation.submit@1.0.0` (design only) | No |
| Enki, Thoth, Nabu definitions | Explicit `AgentToolRef`s (`conversation`, and for Enki `manuscript` and `reasoning`) | **Yes: a governed definition-revision capability does not exist** (the Store is insert-only; V1 PATCH must not be used). Requires approval |
| Policy | None | No |

## 8. Proposed implementation sequence

| Milestone | Scope | New authority | Depends on |
| --- | --- | --- | --- |
| **CONVERSATION-0.1** | Approve this ADR; `ConversationToolConfig` and activity contract schemas; tests of contracts only | No | — |
| **AGENT-CTX-0.1** | Replace `agents/enki_context/` with `activities/`: registries, resolvers, `agent.creation_interview@1.0.0`, partial unique index, successor reservation, evidence outbox, Nataraja pending-evidence finding; port the 15 tests | No | CONVERSATION-0.1 (activity contract schema) |
| **CONVERSATION-0.1A** | Register `conversation@1.0.0`; conversation profile replaces the hardcoded gate (compatibility profile for existing Agents); `chat_v2` internal; `data.validation` coherence rule; Nataraja `implicit_conversation` finding | No | CONVERSATION-0.1 |
| **ENKI-COMP-0.1** | Governed definition revision (draft → G3.3 → approval → Store revision), then explicit Tool refs for Enki (and Thoth) | **Yes**, approval required | CONVERSATION-0.1A |
| **PTAH-0.1a** | Interaction registry, `interaction.submit`, `conversation.submit@1.0.0`, `draft.proposal.decide@1.0.0`, typed results | No | AGENT-CTX-0.1, CONVERSATION-0.1A |
| **ENKI-1.0-B** | Generic coordinator running the Agent's configured activity contract under the 60 s deadline | No beyond existing grants | All above (ENKI-COMP-0.1 for explicit configuration; the compatibility profile allows earlier testing) |

AGENT-CTX-0.1, CONVERSATION-0.1A and PTAH-0.1a can proceed without new authority.
ENKI-COMP-0.1 is the only approval gate.

## 9. Decisions requiring approval

1. The five-concept taxonomy (§1) and the no-dedicated-infrastructure rule for ordinary
   Agents.
2. `conversation@1.0.0` grouping the existing `chat.context` and `chat.response` (no new
   signal), and its configuration fields (`modes`, `history_window`, `activities`).
3. The temporary compatibility profile, and its removal criterion (ENKI-COMP-0.1).
4. Package `activities/`, collection `coll_activity_contexts` and the generic
   `activity.context.*` events. Keep or rename the `conversation_context` audit subject
   (proposed: `activity_context`).
5. The activity contract and resolver registries, and the
   `agent.creation_interview@1.0.0` contract owned by the drafts/Manuscript domain.
6. The partial unique index, successor reservation and evidence outbox (§6).
7. A governed definition-revision capability for existing Agents (new authority).
8. Renaming the PTAH-0.0 interaction to `conversation.submit@1.0.0`.

## 10. Approval conditions (2026-10-31) and AGENT-CTX-0.1 outcome

The AGENT-CTX-0.1 authorization added these conditions:
- source, tests and documentation only;
- no new capabilities, grants, policy, live data, Agent revisions, Ptah runtime or
  Conversation Tool registration;
- keep verified human and Agent session authority, with no System Citizen access;
- delegate every ownership and state check to the owning services;
- typed slots, not arbitrary dictionaries;
- storage-enforced active-generation uniqueness and durable successor intent, or stop
  and report the blocker;
- an atomic pending-evidence obligation with authorized, bounded reconciliation;
- no Redis/Mongo atomicity claims.

Differences between the implementation and §4–§6 above:
- **Single-active enforcement:** a per-scope **head document** (`_id` = scope) instead of
  a partial unique index. Its active pointer and the full activation intent change in
  one compare-and-set, which gives the guarantee using only `_id` uniqueness (MongoDB /
  Cosmos parity; Cosmos not tested).
- **Successor intent:** lives on the head, not on the closed predecessor.
- **Owner fence field names:** `created_by` / `last_owner`.
- **Audit subject:** renamed `activity_context`.

ENKI-CTX-0.1 (`agents/enki_context/`, `coll_enki_conversation_contexts`,
`app.state.enki_contexts`, `enki.context.*`) is **superseded and removed**.

## 11. CONVERSATION-0.1A outcome (2026-10-31)

Implemented as §3 describes, with these differences:
- **Modes:** `context` and `llm`, matching the existing `ClientEventV2.response_mode`, not
  `response`.
- **History:** a `history` policy (`max_messages` ≤ 24, `max_characters` ≤ 32768)
  instead of `history_window` 0–50. It can only narrow the existing projection bounds,
  which the projection now enforces.
- **Gate:** `resolve_conversation` / `ConversationGate` instead of
  `conversation_profile`. Type and Role specs declare nothing; the only type/role data is
  the temporary compatibility table.
- **Compatibility:** exact reviewed `(type, role)` entries: `chat/general` (also a
  missing role), `chat/thoth` and `form/enki`. The proposal (§3.6) covered every `chat`
  Agent, which would infer from type alone.
  - Compatibility modes are both modes, not derived from grants: composition and
    authority stay independent, and grants are still checked on every turn.
  - New `chat` Agents with other roles must attach the Tool.
- **`data.validation` coherence:** a `chat` component requires a resolvable composition
  (attachment or compatibility), not strictly an attachment, so existing compositions
  still validate.
- **Dispatcher:** now checks composition for both modes, inside the journaled turn. The
  `chat.response` provider keeps its own check.
- **Catalog:** only `modes` is presented. An `activities` multiselect needs the trusted,
  startup-built activity registry at projection time and is deferred.
- **`chat_v2@2.0.0` exposure:** unchanged; this milestone replaces or hides no legacy
  Tool.
- **Nataraja:** an informational `conversation` resource (`implicit: true` for
  compatibility, status `passed`), not an `implicit_conversation` finding, so healthy
  reports keep their status. An invalid explicit attachment is a
  `conversation.attachment_invalid` warning.
- **Activity Context corrections (§4 boundary):**
  - `ActivityActor` no longer hard-codes `chat.response`; evidence provenance uses the
    owner kind's own signal.
  - `ActivityContextService.current()` separates a materialized generation from a
    decided successor awaiting reconciliation.
  - Declared activity contracts must be registered and allow the `conversation.v2`
    owner. The Activity Context package does not depend on the Tool.
- **Pre-existing caveat:** the V1 Agent PATCH route can still change `type`,
  `role_name` and `tools` of V2 definitions. This grants nothing, but governed revision
  (ENKI-COMP-0.1) should close it.

No new capability signal, grant, policy revision, trusted line, migration or live Agent
change.

## 12. AGENT-DEF-SEC-0.1 outcome (2026-10-31)

A narrowly scoped security correction: no revision path, grant, policy or live change.

- **Legacy mutation closed:** V1 `PATCH`, V1 `DELETE`, admin PDF collection linking and
  the legacy store adapter refuse governed definitions (`runtime_version` ≠ `v1`, a
  `store` record or a trusted seed) with `409 AGENT_DEFINITION_GOVERNED`.
  - The check runs after the existing access checks, so it discloses nothing new.
  - There is no bypass for any Agent or System Citizen.
- **Compatibility containment (supersedes the §11 `(type, role)` table):** the temporary
  allowlist binds each entry to an immutable `(tenant, Agent ID)`: Enki and Thoth
  (policy-pinned) and Nabu. Each entry is valid only with its reviewed `(type, role)`.
  - New Agents never obtain implicit conversation, whatever their Type/Role.
  - Retirement is unchanged: governed revision attaches `conversation@1.0.0`, then
    entries are removed once Nataraja shows no `implicit` resource.
- **Creation decoupled from conversation:** `V2CompositionPolicy.creation_compositions`
  keeps the accepted creation outcomes and default presentation (`chat/general`,
  `chat/thoth`, `form/enki`), so composer output and the pinned Enki/Thoth candidates
  are unchanged.
  - A new Agent created that way without the Tool presents Conversation but cannot
    converse; Nataraja warns with `conversation.presented_without_composition`.
- **Remaining:** governed revision (ENKI-COMP-0.1) is now the *only* way to change a
  governed definition, and it doesn't exist yet. Workflow edits through V1 still affect
  the Agents that reference them.

## 13. Governed revision (AGENT-REV-0.1, proposed)

[ADR-0005](ADR-0005-governed-agent-definition-revision.md) proposes the Agent-neutral
governed revision capability that ENKI-COMP-0.1 depends on:
- immutable, numbered revision records, with the Agent document as their projection;
- digest-bound human review and approval;
- application by Genesis through a scoped `store.revise.approved@1.0.0`;
- the Enki and Thoth migration targets and the allowlist retirement criteria.

It is not approved or implemented. §8's ENKI-COMP-0.1 row is refined there into REV-1 to
REV-5 plus ENKI-COMP-0.1.

## 14. MIG-FIX-0.1 outcome (2026-10-31): fail-closed activity consent

A narrowly scoped correction found by the MIG-PRE-0.1 readiness review: no signal, grant,
policy, Agent or runtime authority changed.

- **Gap:** REV-5 activity consent treated legacy compatibility as unrestricted, and an
  unwired declaration source or an owner kind without consent as permission. Thoth and
  Nabu would have consented to the creation interview, and to any conversation-owned
  contract registered later. No production path started an activity, and consent never
  replaced UTU authorization, so nothing was reachable (`SECURITY_FINDINGS.md`
  ACTCONSENT-1).
- **Explicit legacy activities:** `LegacyConversationEntry.activities` is an exact,
  reviewed tuple validated like an attachment. Enki declares
  `agent.creation_interview@1.0.0`; Thoth and Nabu declare none. Modes are unchanged, and
  an unregistered declared contract never changes a mode.
- **Fail closed:** `ConversationGate.declared_activities` always returns an explicit set
  (kept only while each contract resolves for `conversation.v2`). `ConversationOwner`
  denies without a source or without a set. `ActivityContextService` denies a new
  generation when the owner kind defines no consent (`OwnerResolver.permits_activity`).
- **Unchanged:** reads, advances, closes, replays and recovery of decided intents; the
  owner's UTU authorization runs first on every call; the activities package still does
  not depend on the Conversation Tool or on any Agent identity.
