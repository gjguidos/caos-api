# Genesis 0.2.1: Conversation History and Agent Cognitive Execution Contract

## Inspected implementation and scope

V1 `agents/mixins/history.py`, `history/store.py`, `agents/mixins/memory.py`,
`memory/store.py` and `cache/helpers.py` remain unchanged. Their exact keys are:

- `<redis_prefix.rstrip(':')>:<redis_key_ns>:<tenant-or-public>:history:v<redis_history_key_version>:<user>:<agent>:<session-or-default>[:tool:<tool>]`
- `<redis_prefix.rstrip(':')>:<redis_key_ns>:<tenant-or-public>:mem:v<redis_memory_key_version>:<user>:<agent>:<session-or-default>:<namespace>:<name>`

AgentMemoryStore supplies `namespace:name` to `memory_key`. V1 history uses
LPUSH/LTRIM, a configured TTL and oldest-first fetch; memory is JSON KV with an
optional configured TTL. Existing mixins do not pass tenant explicitly. Their
public fallback and tenancy toggles are not reused as V2 authorization rules.
RedisRepositoryImpl passes keys unchanged; it supplies no implicit prefix.

Genesis 0.2 already supplies ConversationV2, the Redis turn journal/fence, current
UTU/session authorization, CPBX execution permits, Khepri and ManagedAgentV2.
This slice extends those paths. It adds no autonomous LLM, persistent memory,
consolidation, new broker, speculative orchestrator or replacement agent runtime.
The cognitive contract is preparatory; no AgentMemory abstraction is necessary.

## Exact Redis keys

Let `P = ConversationRepositoryV2.prefix` (default
`caos:communication:v2`) and
`H = sha256(json.dumps([tenant_id, conversation_id]).encode()).hexdigest()`.
Python json.dumps uses default separators, including the space after comma.
Braces in the following keys are literal Redis Cluster hash tags.

| Key | Type | Meaning |
| --- | --- | --- |
| `P:{H}:document` | JSON string | Existing immutable conversation document |
| `P:{H}:events` | hash | Existing per-user idempotency and terminal response journal |
| `P:{H}:turn` | string | Existing conversation-wide in-progress fence |
| `P:{H}:history:v1:messages` | list | New canonical attributed transcript, oldest first |

`history:v1` versions this new schema inside communication V2, independently of
legacy V1 history settings. Mandatory tenant is always part of H; no public
fallback exists. Repository prefix overrides apply to all these keys. The socket
lease remains `P:socket:<sha256(json.dumps([tenant,session]))>` with its existing
15-second lease. Transcript, documents and journals have **no TTL** and survive
logout, session expiry, socket closure and runtime sleep. Redis persistence and
no-eviction settings still determine infrastructure durability.

## Transcript, attribution and idempotency

The transcript is distinct from the bounded turn journal and agent context.
It is never fabricated from journal requests or nested capability context.
Older 0.2 journal entries have no backfill: their transcript is truthfully empty.

ConversationMessageV2 is immutable and includes ID, one-based sequence, tenant,
conversation, event, initiating turn user, participant, content, provenance kind,
capability address, user and agent audiences, and outcome. User messages, genuine
agent conversational responses, deterministic capability contributions and internal
events are different kinds. Internal events remain operational/journal data and
are not returned by the history service. `chat.context` contributes context/hints;
it is never a conversational assistant response.

For dispatcher entries define
`I = sha256(json.dumps([verified_user_id,event_id]).encode()).hexdigest()`.
The user message ID is `I-user`; contributions in requested target order are
`I-0`, `I-1`, etc. The turn user disambiguates per-user event IDs. The verified
user is the input participant; the executing agent is the contribution participant,
with `capability=chat.context` retaining deterministic provider provenance.

A successful turn commits its user message and all ordered target contributions
in the same Redis Lua operation as its terminal journal response and fence release.
The script checks turn ownership and the authorized document snapshot, derives
sequences from list length and appends the complete batch. It preserves validated
message JSON because Redis 6 cjson re-encoding turns empty arrays into objects.
There are no separate sequence/deduplication artifacts. Replays, conflicts, busy,
pending and full admissions never append. Existing journal capacity remains 256
admitted events per conversation, without eviction or automatic crash recovery.

Cancelled, denied, timed-out and failed turns settle only in the journal, with
**no transcript batch**, including no partially completed multi-agent contributions.
Local request status is separate from durable messages. A crash before commit
retains the existing pending fence for trusted recovery; a crash after commit
replays the terminal response. Storage failure fails closed rather than releasing
an uncertain fence. Cancellation cannot undo a batch already committed by Redis;
that atomic operation is the commit point. Admission and submitted terminal writes
are drained on task cancellation so normal cancellation does not abandon a fence.

## Current authorization and visibility

ConversationHistoryV2 reads in the verified session tenant, checks current UTU
membership, reads messages, and repeats authorization/document comparison before
output. A changed document denies the read. Agent projection additionally requires
current agent approval, session allowed-agent membership and chat.context grants.
It runs under the runtime lock immediately before NCSC invocation. CPBX still
checks its live permit independently. Identity and grant evidence never travel in
client JSON or the capability payload.

Empty `visible_user_ids` means all **current conversation members**, never public.
Nonempty user audiences further restrict membership. Agent audiences are explicit;
empty means no agent projection. Projection requires visibility to both the
initiating user and target agent. Dispatcher input is visible to members and
selected agents. Capability results are restricted to the initiating user because
context can include that user's filtered history. No client append endpoint can
invent assistant responses or set audience fields. Repository reads/writes are
trusted storage operations; authorization belongs to the history service/dispatcher.

The final conversation document CAS protects against membership changes at commit.
UTU permit checks occur immediately before commit; they are not a distributed
transaction with transcript storage, since revocation uses a separate tenant slot.
A revocation observed before completion suppresses output and the transcript batch.
Agent revocation prevents projection/execution; it does not erase historical
messages already visible to current human members. Existing architecture has no
public governed conversation membership or per-message audience mutation endpoint;
none is added here.

AgentHistoryViewV2 scans at most the last 100 canonical rows, filters visibility
and conversational provenance, excludes the current user's current event, and
selects at most 24 messages and 32,768 characters. It retains order and provenance,
then adapts conversational role/content to ChatToolV2. Responses from other agents
retain an explicit `[agent:id]` content attribution within the existing schema. Context blobs never
project as assistant answers or recursively amplify history. Older visible rows
outside the bounded scan can intentionally be omitted. This is context, not memory.

## API and client

`GET /api/v2/agentverse/communication/conversations/{id}/history` accepts optional
`cursor` and `limit` (default 50, range 1–100). Existing Genesis authentication and
UTU apply. Responses are no-store; denial/unavailability returns 403, invalid
cursor/window returns 422. Payload: `{messages:[...],next_cursor:string|null}`.

An opaque URL-safe base64 cursor contains `[H,last_scanned_sequence]`. It is a
position, not authority, and is rejected across tenant/conversation scopes.
Each page scans at most `limit` canonical rows, filtering before output. A page
can be empty with a next cursor. A full final page may require one empty request
to reach null. Append-only sequences prevent offset drift under concurrent appends;
every page rechecks current authority.

The Next route `/api/caos/v2/conversations/{id}/history` forwards bounded cursor
and limit only to the fixed configured upstream with the existing server bearer,
no redirects and no-store. It whitelists message fields, excluding permits,
credentials and audience metadata. Workspace loads history on reopen/refresh/turn
settlement; stable IDs merge pagination. Aborted/stale responses cannot replace
another conversation's history. Denial clears rows. Session-owner changes reset
the workspace. Only recent conversation IDs use localStorage, never message text.
Socket context responses do not manufacture transcript messages.

## Immutable AOSA cognitive contract

`entities/cognitive_v2.py` defines AgentCognitiveProfile, CognitiveTurn,
CognitiveContext and CognitiveResult. The frozen profile preserves type, role,
personality, rule packs, configured capabilities, cognitive resource references
and definition version. Resource identifiers are declarative strings, not the
legacy mutable CognitiveResources infrastructure handles.

The trusted-only frozen context separates verified initiating user/agent identity,
bounded history and UTU AuthorizedAgentPrincipal from content. Its authorized
capability snapshot must be a subset of configured capabilities. No model, profile
or serialized capability list grants authority; CPBX/UTU still validate actual live
grants/permits on every invocation. CognitiveResult distinguishes conversational
responses, capability contributions and internal events; contributions must retain
a capability address. The contract is deliberately not wired to fake default
profiles in ManagedAgentV2 and introduces no autonomous executor.
