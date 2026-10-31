# Conversation Tool (CONVERSATION-0.1A)

**Status:** implemented, uncommitted, awaiting architectural review (2026-10-31,
`feature/ops-v2` over `b70ab0b`). Decision basis:
[ADR-0004](architecture/decisions/ADR-0004-conversation-tool-and-activity-context.md) §3 and
§11. **Audience:** engineers working on Agent composition, ENKI-COMP-0.1, PTAH-0.1a and
ENKI-1.0-B.

Conversation is an optional, configurable, first-class CAOS Tool. Whether an Agent can
take part in a V2 conversation is resolved from its registry-backed Tool attachment, not
from its Agent Type or Role. This is not a second chat engine: turns still flow through
the existing V2 transport, dispatcher, turn leases, journal and the `chat.context` /
`chat.response` providers.

**Implemented here:** Tool registration, strict configuration, the registry-driven
resolver at every former gate, a temporary identity-bound compatibility allowlist
(AGENT-DEF-SEC-0.1) and a Nataraja diagnostic. **Not implemented:** governed definition revision (ENKI-COMP-0.1), the Ptah
interaction runtime (PTAH-0.1a) and the coordinator (ENKI-1.0-B).

## 1. Registration (`tools/conversation_tool/`)

| Field | Value |
| --- | --- |
| Identity | `conversation@1.0.0` (exact; resolution requires the published version) |
| Classification | `tool_class: conversational`, `scope: agent`, `exposure: public`, `ownership: global`, `execution_mode: synaptic_only` |
| Optional | Not in `CORE_TOOL_KEYS`, never auto-attached |
| Declared actions | Existing `chat.context@1.0.0` (provider `tool.chat`) and `chat.response@1.0.0` (provider `cognitive.chat`), both `access: granted`, no trusted lines |
| V1 | `execute` and `_run` always raise `EventPermissionError`; no V1 route |
| Startup check | `validate_conversation_tool()` (Genesis bootstrap) fails closed if the registered version or the two actions drift |

**No new capability signal, grant, policy revision or trusted line.** The Tool declares its
relationship to the two existing capabilities; it does not provide or grant them. Legacy
Tools (`chat` V1, `chat_v2@2.0.0`) are unchanged.

## 2. Configuration (`ConversationToolConfig`, strict, frozen, `extra="forbid"`)

| Field | Meaning | Constraints and default |
| --- | --- | --- |
| `modes` | Conversation modes the Agent offers: `context` (`chat.context`) and `llm` (`chat.response`), matching `ClientEventV2.response_mode` | 1–2 unique literals; default both |
| `history.max_messages` | Prior transcript messages a turn may see | 0–24; default 24 (today's projection bound) |
| `history.max_characters` | Prior transcript characters a turn may see | 0–32768; default 32768 (today's bound) |
| `activities` | Exact `id@version` activity contracts this Agent may run in a conversation | 0–8 unique; must be registered in the frozen Activity Contract Registry **and** allow the `conversation.v2` owner kind |

- **Bounds:** history can only narrow the existing projection bounds; the projection
  rejects anything wider.
- **Not expressible:** grants, signals, scopes, trusted lines, providers, prompts,
  instructions, models, tenants, permits or enable flags. Configuration is a restriction,
  never authority.
- **Storage:** the existing sparse `AgentToolRef(key="conversation", config={...})`,
  validated by `data.validation` through the Tool's `config_schema`.

## 3. Registry-driven composition (`agents/conversational_composition_v2.py`)

`resolve_conversation(definition)` is deterministic and read-only:

1. **Not V2:** any definition other than `runtime_version == "v2"` is unavailable.
2. **Explicit attachment:** exactly one `conversation` attachment, the registered
   version `1.0.0` and a valid configuration (plus registered activities, when checked) →
   source `tool`.
   - Duplicate, unpublished, invalid or unregistered-activity attachments are
     unavailable.
   - **An explicit attachment never falls back to compatibility.**
3. **Compatibility:** no attachment and an allowlisted existing definition identity whose
   type and role still match (§5) → source `legacy`, with the default configuration and
   exactly the entry's reviewed `activities` (MIG-FIX-0.1). A Type/Role pair alone never
   matches (AGENT-DEF-SEC-0.1).
4. **Otherwise:** unavailable (`not_attached`).

`ConversationGate` is the runtime form:
- it reads one trusted definition (`find_by_id` only) and denies missing, V1 or
  other-tenant definitions like the V2 runtime;
- it binds once, at trusted startup, to the frozen Activity Contract Registry; until
  bound, declared activities fail closed.

**Availability and authority are independent; a turn needs both:**
- **Composition:** the Tool, or compatibility, must allow the requested mode.
- **Capability:** the Agent's existing UTU grant (`chat.context` or `chat.response`),
  checked on every turn as before.

A Tool may be attached while the Agent lacks the grant (the turn is denied), and a grant
without the Tool or a compatibility entry yields `composition_unavailable`. No runtime
logic branches on Enki's Role or on any Agent's identity.

### Former hardcoded gate locations

The function `supports_v2_conversation(type, role)` is removed. Each former call site now
uses the resolver:

| Location | Before | Now |
| --- | --- | --- |
| `services/cognitive_response_v2.py` (`chat.response` provider) | `type == "chat" or (form, enki)` | `ConversationGate.resolve(definition).require("llm")`; history narrowed by the configuration |
| `services/client_event_dispatcher_v2.py` | No composition check | Per target Agent and mode, inside the journaled turn: `composition_unavailable` is recorded and replayed like any turn error. Leases, fencing, journals and recovery are unchanged |
| `agents/composition/__init__.py` (Genesis composer) | Same predicate | Accepts an explicit attachment or a reviewed **creation composition** (`V2CompositionPolicy.creation_compositions`: `chat/general`, `chat/thoth`, `form/enki`). Output unchanged; no Tool is auto-attached. Creation shape only: a new Agent converses only with the explicit Tool (AGENT-DEF-SEC-0.1) |
| `tools/validation_tool/agent_validator.py` (`_gui`) | Same predicate for a `chat` component | A `chat` component requires an explicit attachment or a reviewed creation composition (presentation) |
| `entities/gui_components.py` (`default_gui_components`) | Mirrored the predicate | The Conversation component follows the explicit attachment (an invalid one presents none) or a reviewed creation composition; Enki's contract form stays a presentation default. Presentation never decides a turn |

## 4. Activity Context boundary

- **Dependency direction:** the Conversation Tool may declare activity contracts. The
  Activity Context package does not import or depend on the Tool or the resolver (tested).
- **Owner-specific grant:** `chat.response` is required only by the `conversation.v2`
  owner adapter. `ActivityActor` no longer fixes a turn signal: Seshat provenance uses the
  owner kind's own `audit_signal` (CONVERSATION-0.1A fix).
- **Reads:** `ActivityContextService.current()` distinguishes a materialized active
  generation from a decided successor still awaiting reconciliation (`materialized`,
  `reconciliation_pending`). `active()` is unchanged.
- **No Workflow integration** was added.

## 5. Legacy compatibility (temporary)

`LEGACY_CONVERSATION_COMPATIBILITY` is the temporary **allowlist**: one entry per
conversational definition that existed before CONVERSATION-0.1A, bound to its immutable
identity (AGENT-DEF-SEC-0.1). An entry applies only while the definition still has its
reviewed type and role.

| Entry | Tenant / Agent ID | Type / Role | Definition | Modes | Activities |
| --- | --- | --- | --- | --- | --- |
| `legacy.v2.enki` | `caos-local` / `…0004` (policy-pinned) | `form` / `enki` | Enki | both | `agent.creation_interview@1.0.0` |
| `legacy.v2.thoth` | `caos-local` / `…0006` (policy-pinned) | `chat` / `thoth` | Thoth | both | none |
| `legacy.v2.nabu` | `caos-local` / `697e8c4b-…` | `chat` / `general` | Nabu | both | none |

**Rules:**
- **Identity, never Type/Role:** a newly created Agent (including a new `chat/general`,
  `chat/thoth` or `form/enki` Agent), an allowlisted ID in another tenant, or an
  allowlisted definition whose type or role changed gets no compatibility. New Agents
  converse only with the explicit Tool.
- **Immutable identity:** governed definitions cannot be changed or deleted through legacy
  routes ([Agent Definition Governance](AGENT_DEFINITION_GOVERNANCE.md)), so an entry
  cannot be redirected.
- **Re-provisioned slots:** a fresh environment's policy-pinned Enki and Thoth have the
  same identities and are covered. Any other Genesis-created `chat/general` Agent (local
  or elsewhere) is not; Nataraja reports it as
  `conversation.presented_without_composition`.
- **Grants unchanged:** compatibility preserves existing effective chat behavior and
  confers nothing. Nabu (`chat/general`, no grants) is composable but every turn is still
  denied.
- **Exact activities (MIG-FIX-0.1):** each entry declares its activity contracts
  explicitly, validated like an attachment (`LegacyConversationEntry.activities`). Only
  Enki declares its existing creation interview; Thoth and Nabu declare none. A contract
  registered later is never implied, and an unregistered declared contract changes no
  conversation mode (it is simply not consented to).
- **No other capabilities:** compatibility declares no Manuscript, Reasoning, Catalog or
  Draft capability; Enki's activity declaration grants none of them (its existing UTU
  grants still decide).
- **Explicit wins:** an explicit attachment always replaces the compatibility entry.

**Retirement** (proposed in detail in
[ADR-0005 §5](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)):
1. Governed definition revision (ADR-0005 REV-1 to REV-4: draft → validation → human
   approval → Store revision) is implemented, then ENKI-COMP-0.1 applies it. Each
   identity's creation baseline must first be `verified` by the REV-1 Nataraja
   `definition_baseline` check (implemented).
2. Each allowlisted definition (Enki, Thoth, Nabu) receives an explicit
   `conversation@1.0.0` attachment through that revision.
3. When Nataraja reports `source: tool` for that identity and a turn in each declared
   mode has been verified, the entry is removed from the table in a reviewed change.
   Nabu's entry stays until its owner revises it or the architect retires it.
4. After the last entry is removed, the resolver has only the explicit path.

**Closed (AGENT-DEF-SEC-0.1):** the legacy V1 Agent `PATCH` / `DELETE` routes, the
collection-link path and the legacy store adapter now refuse governed definitions
(`409 AGENT_DEFINITION_GOVERNED`). Only governed revision (ENKI-COMP-0.1) may attach the
Tool.

## 6. Nataraja diagnostic

`nataraja inspect` / `reconcile` reports one `conversation` resource
(`id: conversation:<agent>`) per V2 definition with a resolvable composition:
- **Facts:** `source` (`tool` or `legacy`), `implicit`, `compatibility` (the entry ID),
  `modes`, `activities` and, when implicit, `retire_by`.
- **Status:** `passed`, so implicit compatibility does not degrade an otherwise healthy
  report.
- **Invalid attachment:** an explicit attachment that cannot compose adds the
  `conversation.attachment_invalid` warning with its internal reason.
- **Presented without composition (AGENT-DEF-SEC-0.1):** a definition whose effective GUI
  presents Conversation but which has neither the Tool nor an allowlist entry (for example
  a new `chat/general` Agent) is a `warning` resource with `source: none`, plus a
  `conversation.presented_without_composition` finding.
- **Non-conversational Agents** are not reported.
- **What is observed:** only Conversation Tool attachments are read from `coll_agents`.
  Grants, signals, scopes, providers and other Tool configurations are never reported.

### 6.1 GUI consistency decision (proposed, ADR-0005 §6)

- **New human-reviewed definitions** (G3.3/G3.4 creations and revisions) will require a
  valid explicit `conversation@1.0.0` attachment for any declared or defaulted `chat`
  component; otherwise review is ineligible with `gui.conversation_unavailable`.
  - **Revisions (REV-2):** implemented as `revision.conversation_required`, including
    activity contracts checked against the frozen registry
    (`revision.conversation_activity_unavailable`).
  - **G3.3/G3.4 creations:** still pending.
- **No auto-attach:** the human adds the Tool in the draft.
- **Pinned Genesis outputs are unchanged.**
- **Presentation:** descriptors will expose each component's coarse `availability`.

Not implemented.

## 7. Catalog and Ptah readiness

- **Catalog:** the existing projections return identity, description, tags,
  `tool_class: conversational` and a `modes` multiselect with descriptive options.
  - `history` and `activities` are not presented to requesters yet; they are set through
    validated attachment.
  - Providers, signals, the config-schema path, execution mode and grants are never
    exposed.
  - Catalog entries state `execution_authorization: not_evaluated`.
- **Ptah binding (design only):** the approved generic interaction identity is
  `conversation.submit@1.0.0`, replacing PTAH-0.0's `enki.conversation.submit@1.0.0`
  ([PTAH-0.0 §2.4](PTAH_0_0_INTERACTION_CONTRACTS.md)). It binds to an Agent's declared
  GUI `chat` component (`interface_key`). The server resolves it only if:
  - the Agent's conversation composition is available and allows the requested mode;
  - the Agent's existing grant authorizes the turn;
  - the chat component is among the Agent's effective GUI components.

  No Ptah runtime, interaction registry or WebSocket interaction frame is implemented.

## 8. Verification (2026-10-31, disposable MongoDB and Redis)

**Tests:**
- `tools/conversation_tool/tests/test_conversation_tool.py` (67, no database):
  registration, exact version, startup drift, strict and bounded configuration, safe
  catalog, V1 denial, the resolver matrix, compatibility, activity binding, the runtime
  gate, former call sites, GUI defaults, `data.validation` and Nataraja.
- `tools/conversation_tool/tests/test_conversation_tool_integration.py` (8; real Redis,
  UTU, CPBX and dispatcher):
  - compatibility preserves conversation;
  - a grant without attachment fails closed and is journaled;
  - explicit attachment composes an ordinary Agent;
  - mode restrictions;
  - attachment without a grant is denied;
  - an ungranted compatible Agent acquires nothing;
  - the history policy narrows the model's input.
- Plus a Genesis composer test, the ported fixtures and the Activity Context `current()`
  and owner-provenance tests.

**Results:** full backend 3039 passed, 11 skipped and 5 inherited root-signing failures.

**AGENT-DEF-SEC-0.1 (2026-10-31):**
- **Tests:** the identity allowlist, new Agents without the Tool (unit and a runtime test
  with the production allowlist) and the Nataraja presentation warning.
- **Fixtures:** the shared response fixture models an allowlisted existing definition
  through an injected, test-only allowlist; the production table is unchanged.
- **Results:** full backend 3103 passed, 11 skipped, 5 inherited root-signing failures.

## 9. Turn-bound composition (REV-5)

- Each turn's Tools, modes and history policy come from the **one** definition read at
  admission (`ConversationGate.admit`), which also yields the turn's `DefinitionBinding`.
- Before each capability request the binding is re-verified (`ensure_current`, then the
  `chat.response` provider's own read). A changed definition yields `definition_changed`
  (retryable as a new turn); a removed Tool or mode applies to every new turn.
- The binding is journaled with the outcome (`definitions`). It never grants anything:
  UTU grants are rechecked on every turn.
- Activity contracts declared by an explicit attachment gate new Activity Context
  generations (`start` / `switch`). Legacy compatibility consents only to its entry's
  reviewed activities (MIG-FIX-0.1: never unrestricted). See
  [ADR-0005 §17](architecture/decisions/ADR-0005-governed-agent-definition-revision.md).
