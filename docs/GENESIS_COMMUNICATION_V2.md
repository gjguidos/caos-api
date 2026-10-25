# Genesis 0.2: persistent conversations and session communication

This incremental slice adds `/ws/api/v2/caos` alongside the unchanged V1 routes.
It returns Adam's existing deterministic `chat.context` contribution, including
hints and usage metadata. It does not generate an assistant answer or call an LLM.
The existing context provider currently returns the supplied history (empty in
this slice); the current text is passed to the capability and journaled.

## Execution and identity

1. A browser asks the Next server for a single-use ticket. The server forwards
   the exact provisioned bearer to POST `/api/v2/agentverse/communication/ticket`.
2. The session authority verifies the bearer, durable session and fingerprint.
   It stores only the verified caller binding under a hashed random ticket key,
   expiring after 30 seconds. Ticket redemption atomically consumes the key and
   rechecks the session; a session UUID alone cannot authenticate.
3. The browser opens `/ws/api/v2/caos` and sends
   `{"type":"authenticate","ticket":"..."}` within five seconds. Credentials
   never appear in its URL. The server replies `session.ready`. A renewable
   Redis lease admits one V2 communication connection per authenticated session.
4. UTU checks the persisted conversation's tenant and user membership, every
   target's participation, the session's allowed agents, ownership, enabled
   approvals, policy, durable permits and `chat.context` grant before dispatch.
5. Khepri receives the verified **user** lifecycle key. Each ManagedAgentV2
   receives its own authorized NCSC client; CPBX executes under that **agent's**
   principal with the event ID as correlation and causation. A participant list
   cannot grant execution, and one participant cannot borrow another's agent.

There is no new capability registry, event bus or orchestration engine. Providers
are lazy singletons in the existing AgentVerse DI container. Routes are discovered
by the existing router loader. Existing Genesis opt-in configuration still applies.

## Conversation and event contracts

POST `/api/v2/agentverse/communication/conversations` accepts only
`{"agent_ids":["<adam-id>"]}`. Tenant and creator membership come from the verified
session. GET the same path with `/{conversation_id}` to retrieve it while authorized.

`ConversationV2` persists its own ID, tenant, member user IDs, agent participant
IDs and `members` visibility, independently of sockets and agent runtimes.
The public creation route adds only its authenticated creator. Multiple user
members are supported by the entity and trusted repository writer and tested;
invitation, membership editing and cross-owner agent delegation are future work.

Client message:

```json
{
  "conversation_id": "<conversation-id>",
  "event_id": "<unique-request-id>",
  "type": "chat.message",
  "agent_ids": ["00000000-0000-4000-8000-000000000002"],
  "message": "Hello Adam"
}
```

Response:

```json
{
  "conversation_id": "<conversation-id>",
  "event_id": "<server-event-id>",
  "correlation_id": "<unique-request-id>",
  "type": "chat.context",
  "results": [{"agent_id": "<adam-id>", "context": {"hints": {}, "usage_meta": {}}}]
}
```

The example abbreviates the context. Each response contains ordered per-target
results. Fanout is sequential and bounded to four targets. Failure returns a
correlated `event.error` with a stable code, never an exception or partial context.
Cancellation is connection-local:

```json
{
  "conversation_id": "<conversation-id>",
  "event_id": "<cancel-request-id>",
  "type": "event.cancel",
  "cancel_event_id": "<original-request-id>"
}
```

The server acknowledges with `event.cancelled`, correlated to the cancel request
and identifying the original event. Cancellation of admitted work also journals
its terminal error. Repeated cancellation is harmless; no other socket's work
can be cancelled through this control message.

## Persistence, concurrency and failure boundaries

The new repository reuses the configured Redis repository, like durable Genesis
sessions. Conversation documents and turn journals have no TTL. Deployment must
retain Genesis's authenticated, durable, no-eviction Redis configuration.

- Redis atomically admits one turn per conversation, across workers/users.
  The local socket queue runs turns in order; a competing connection receives
  `busy` and must explicitly retry. Runtime locks also serialize use of an agent
  across local conversations. There is no distributed runtime migration.
- At most eight chat submissions wait or execute per socket, with a 16 KiB
  application frame bound, 8 KiB text bound, ten-second deterministic turn budget
  and two-second send/storage deadlines. Overflow returns `backpressure`.
- Event IDs are scoped by tenant, conversation and user. A payload digest rejects
  conflicting reuse. Completed duplicates return the exact saved response JSON
  without invoking an agent again, after fresh authorization checks. Pending
  duplicates return `pending`. This is admission/replay protection, not a claim
  of exactly-once side effects.
- The durable journal keeps up to 256 admitted turns per conversation. It rejects
  further new IDs with `full`; it never evicts idempotency evidence silently.
  Requests and responses are journaled. A transcript/history API and pagination
  are deferred; current dispatch supplies empty history to `chat.context`.
- Disconnect cancels and joins this socket's work and releases its connection
  lease. Conversations and session-owned runtimes survive disconnect. Runtime
  revocation and shutdown remain owned by UTU/Anubis; disconnect is not logout.
- Local and remote UTU revocation continue to cancel CPBX work and close runtime
  gates. The socket checks session validity and connection ownership every 250 ms,
  and checks again before output. Redis loss fails closed.
- Cancellation unwinds deterministic execution before completing its journal.
  Process death or uncertain journal completion leaves the conversation turn
  fenced, rather than allowing a second worker to overlap unknown work. There is
  intentionally no automatic stale-turn unlock. Trusted recovery must establish
  quiescence before resolving the pending event; no public recovery API is added.

## Opt-in browser client

The actual client checkout is `/Users/guidoguidos/Development/caos/caos-client`.
New files add POST `/api/caos/v2/ticket` and `/api/caos/v2/conversations` server
proxies and `src/shared/websocket/caosV2.ts`. Existing V1 hooks are unchanged.
Set the server-only client variable:

```dotenv
GENESIS_COMMUNICATION_URL=http://127.0.0.1:8001/api/v2/agentverse/communication
```

The proxies require a same-origin POST and signed browser session, forward the
exact retained bearer, reject redirects, time out and disable caching. The ticket
response whitelists ticket fields so it cannot expose an upstream bearer.

Create a conversation through the proxy, then use the transport:

```ts
import { CaosV2Client } from "@/shared/websocket/caosV2";

const created = await fetch("/api/caos/v2/conversations", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ agent_ids: [adamId] }),
});
if (!created.ok) throw new Error("Conversation creation failed");
const conversation = await created.json();
const communication = new CaosV2Client("ws://127.0.0.1:8001");
await communication.connect();
const result = await communication.sendChat(conversation.id, [adamId], "Hello Adam");
// Share this instance across conversations at session scope.
// Call communication.close() on logout, session replacement or owner disposal.
```

Use `wss` with HTTPS. The helper correlates conversation/event responses, bounds
pending work, rejects pending requests on disconnect, and obtains a fresh ticket
on explicit reconnect. It never silently resubmits indeterminate work. The backend
also enforces the single connection rule. No existing chat UI is switched to V2;
UI/session-provider integration is a follow-up to this opt-in transport slice.
No environment files or running API source mounts were changed; no services were restarted.

## Verification and remaining work

Backend tests live in `src/domains/agentverse/tests/communication/` and use unique
Redis keys. Set `UTU_TEST_REDIS_URL` to the configured development Redis when
running them in the existing API container. They cover authenticated HTTP and
ASGI WebSockets, deterministic context, exact replay, conflicting IDs, tenant and
membership denial, unauthorized targets/grants, multiple users/agents, distinct
principals, connection exclusivity, concurrency, capacity, cancellation,
disconnect and local/remote revocation. Existing lifecycle/NCSC tests are retained.
Client tests cover ticket mediation, origin/session denial, correlation,
backpressure, cancellation, disposal and reconnect.

Future work: streamed agent output, conversation transcript/history APIs,
membership administration, shared-agent execution policy, recoverable turn
supervision, coordination/delegation recipes, runtime migration, and mounting the
V2 transport in a session-owned UI provider. None are permissions implied by
conversation participation.

## Changed files

API checkout (relative paths):

- `src/domains/agentverse/entities/conversation_v2.py` — conversation and message contracts.
- `src/domains/agentverse/repositories/conversation_v2.py` — persisted documents, turn journal and connection leases.
- `src/domains/agentverse/services/client_event_dispatcher_v2.py` — authorized lifecycle/CPBX dispatch.
- `src/domains/agentverse/interfaces/api/v2/communication.py` — ticket/conversation HTTP API and V2 WebSocket.
- `src/domains/agentverse/security/sessions.py` — issue/redeem one-use caller-bound tickets.
- `src/domains/agentverse/security/utu.py` — conversation visibility and target authorization.
- `src/domains/agentverse/dependencies/di_container.py` — lazy repository/dispatcher providers.
- `src/domains/agentverse/tests/communication/test_communication_v2.py` — 18 focused contract/integration cases.
- `docs/GENESIS_COMMUNICATION_V2.md` — protocol, setup, limits and handoff.

Client checkout (relative to `/Users/guidoguidos/Development/caos/caos-client`):

- `src/shared/auth/lib/genesis-communication.ts` — same-origin, authenticated, size-bounded proxy helper.
- `src/shared/auth/lib/genesis-communication.test.ts` — proxy authorization, privacy and body-bound tests.
- `src/app/api/caos/v2/ticket/route.ts` — server-mediated ticket route.
- `src/app/api/caos/v2/conversations/route.ts` — authenticated creation proxy.
- `src/shared/websocket/caosV2.ts` — opt-in session communication transport.
- `src/shared/websocket/caosV2.test.ts` — connection, correlation, backpressure and cancellation tests.

No V1 route, event handler, authentication helper or chat hook was edited.


## Verification results

Checks ran against an isolated source copy in the existing API container using
unique development Redis test keys, without changing its live source mount.

- Backend: **170 passed**, including 18 new V2 cases, existing lifecycle/UTU/NCSC
  coverage and the shared WebSocket authentication middleware tests.
- Client: **56 passed** across both new test files and the three existing
  WebSocket helper suites; full TypeScript check and targeted ESLint passed.
- New Python modules/tests pass targeted Ruff lint; all eight touched Python
  files pass formatting checks. Touched existing files retain two baseline lint
  findings: executable `di_container.py` without a shebang (EXE002) and the existing
  retry handler's broad exception catch in `sessions.py` (BLE001). Both were
  reproduced on the committed HEAD copy.
- The separate existing V1 teardown suite has **12 failures** caused by its
  existing identity/mock assumptions. The exact same failures reproduce on an
  untouched committed HEAD copy (12 failed, 14 passed with its middleware checks).
  V1 was deliberately left unchanged.
- No commit was created. Live browser/UI mounting and deployed-service testing
  were not performed; the registered ASGI WebSocket path is covered end to end
  through real session authority, Redis, UTU, Khepri, runtime and CPBX.
