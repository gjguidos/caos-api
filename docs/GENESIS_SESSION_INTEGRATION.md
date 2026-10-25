# Genesis session integration and Redis maintenance

## Local development status — 2026-10-25

The actual `caos-api` and `caos-client` development environments now enable
Genesis sessions and Adam-only trusted provisioning. Production was not touched.
Default application flags remain disabled for other deployments.

Redis was recreated with its original `caos-api_redis-data` volume. It had zero
keys before maintenance; no reset or deletion was needed and no data was lost.
An RDB snapshot was saved to `/tmp/caos-local-redis-before-genesis.rdb` before
recreation and verified with Redis 6 `redis-check-rdb` (checksum valid, zero keys). This temporary snapshot is not a long-term recovery backup.

The default `docker-compose.yml` now enforces authenticated Redis, protected mode,
AOF, `appendfsync always`, and `maxmemory-policy noeviction`. Anonymous PING is
rejected with NOAUTH. Only `127.0.0.1:6380` is published: a separate host Redis owns
6379 and was left untouched. API containers use authenticated `redis:6379`; the
host Next development server uses authenticated `127.0.0.1:6380`. Private URLs and
shared credentials live only in ignored environment files. The local JWT signing
key was rotated, so tokens from before maintenance fail signature verification.
The optional secure override remains compatible; ordinary Compose commands now
retain the secure defaults without an override.

Read-only verification (from the API checkout):

```sh
docker compose config --quiet
docker compose exec -T api python - < scripts/verify_genesis_redis.py
docker inspect caos-api-redis-1 --format '{{json .Mounts}} {{json .HostConfig.PortBindings}}'
docker compose exec -T redis sh -ec 'unset REDISCLI_AUTH; redis-cli ping'
```

AOF and a successful restart test do not establish power-loss durability or
backup recoverability. Keep the recovery fencing rules in GENESIS_REVOCATION.md.
Never restore stale authority state while admitting old clients. Development
volume resets were authorized for this maintenance but were not needed. Any
future reset must fence old clients, issue fresh sessions and CAS-install a new
tenant policy incarnation. Do not reset production or unrelated volumes.

## Trusted local composition

API settings in the private `.env`:

```dotenv
AUTH_MODE=local
ENVIRONMENT=development
GENESIS_SESSIONS_ENABLED=true
GENESIS_LOCAL_ENABLED=true
GENESIS_SECURITY_POLICY_FILE=/app/config/genesis.local.json
```

Generate a random hex Redis password of at least 64 characters and coordinate it
in `REDIS_PASSWORD` and every consumer's `REDIS_URL`. Do not commit secrets.
`REDIS_LOCAL_PORT` optionally changes the default host port 6380.

`config/genesis.local.json` approves only tenant `caos-local`, owner
`00000000-0000-4000-8000-000000000001`, and Adam
`00000000-0000-4000-8000-000000000002`. Only `chat.context` is granted; there are
no trusted nested lines or LLM grants. Local startup verifies that exact scope,
creates a private V1-schema-compatible Adam definition if absent, and rejects
conflicting persisted ownership. It reads the authoritative Redis policy version
and passes that exact value to UTU `install_policy(expected=...)` when absent or
changed. Identical policy restarts preserve the version; CAS conflicts propagate.
No client request installs policy. The local bootstrap and enrollment bridge
require all four development/auth/Genesis gates and reject production/Entra.

Client settings in private `.env.local` (host Next development server):

```dotenv
AUTH_MODE=local
GENESIS_LOGOUT_REQUIRED=true
GENESIS_LOCAL_SESSION_URL=http://127.0.0.1:8001/api/v2/agentverse/session/local
GENESIS_LOGOUT_URL=http://127.0.0.1:8001/api/v2/agentverse/session/logout
```

The API and client share `LOCAL_JWT_SECRET` (at least 32 characters); the client
also needs its own `SESSION_SECRET`. Open `/api/auth/login` to establish a new
local browser session. The server signs a 15-minute token with fresh UUID `sid`
and signed tenant, then submits it to the local bridge. The bridge independently
verifies the signature, fixed development membership and persisted ownership;
requests cannot choose grants, tenant, owner or allowed agents. The exact token
is retained in the signed HttpOnly browser cookie and reused for API calls.
Refresh requires another login and a fresh sid; there is no silent reprovisioning.
Logout clears the cookie only after backend 204 acknowledgement. V1's existing
local mode remains available with `GENESIS_LOGOUT_REQUIRED=false`.

## Session-bound V2 lifecycle

All calls use the exact provisioned bearer token. Identity is derived from the
verified authority, while `agent_id` is independently checked against approved
ownership and allowed agents:

- POST `/api/v2/agentverse/session/local` — local enrollment; no grants body.
- POST `/api/v2/agentverse/agents/{agent_id}/awaken` — UTU then Khepri.
- GET `/api/v2/agentverse/agents/{agent_id}` — authenticated lifecycle status.
- POST `/api/v2/agentverse/agents/{agent_id}/signals/chat.context` — CPBX-enforced
  signal, with JSON `{"text":"Hello","history":[]}`.
- POST `/api/v2/agentverse/agents/{agent_id}/sleep` — Anubis while authorized.
- POST `/api/v2/agentverse/session/logout` — durable session/UTU invalidation;
  revocation listeners immediately close runtime gates and supervise cleanup.

After logout, caller-driven sleep correctly denies. Trusted Anubis cleanup
removes the runtime and releases its lease without reusing caller authority.
The current V2 runtime provides deterministic context; it has no autonomous LLM
loop. Existing V1 message routing remains separate.

Repeat the real composed Adam check:

```sh
docker compose exec -T api python - < scripts/verify_genesis_local.py
```

It creates and permanently revokes a fresh development session, uses the actual
Mongo definition, Redis presence, factory/runtime and registered context handler,
and verifies retained CPBX client/UTU permit denial plus registry/lease cleanup.
It prints no secrets and leaves terminal authority records for replay prevention.

## Authoritative sessions

`RedisAgentSessionAuthority` uses the existing RedisRepository.eval interface.
Records and expiry/retry index use one Redis slot, have no TTL, and are never
silently removed. Provisioning attests current membership and allowed agent IDs
through trusted server code; no route accepts client-written grants or approvals.
A valid JWT must include signed tid, sid, sub and finite exp. Identity follows the
existing oid-or-sub rule. sid is bound to tenant, owner, expiry and a fingerprint
of the exact verified token. A copied UUID or a different signed token with the
same sid cannot authenticate. A stolen complete bearer token remains a bearer
credential. V1's per-socket UUID and token-derived session keys remain separate.

The login/identity-provider integration must supply a fresh signed sid for each
new authority and call the trusted provision method after checking membership:

```python
sessions = container.agentverse.genesis_sessions()
await sessions.provision(
    verified_access_token,
    tenant_id=trusted_tenant_id,
    user_id=trusted_owner_id,
    allowed_agent_ids=frozenset(trusted_allowed_agents),
)
```

The adapter independently verifies the JWT and checks those identities. All
membership removal/session invalidation writers must use respectively
`await sessions.remove_membership(tenant_id, user_id)` and
`await sessions.invalidate(session_id)`. Membership removal atomically fences
new provisioning and marks existing matching sessions pending; it is terminal
until a future explicit membership-version protocol exists. It currently scans
this namespace in Lua, so review tenant/session scale before production use.
No automatic renewal/re-enablement exists. A refreshed token needs a new sid and
fresh provisioning; an issuer that reuses sid across refreshed tokens is not yet
compatible. Do not weaken the exact token binding to accommodate it.

Invalidation atomically writes pending intent, awaits the registered singleton
UTU handler, then records terminal completion. False from UTU means an already
revoked scope and is a successful idempotent acknowledgement. Failures leave an
indexed pending intent. Every admission and every UTU permit check reads current
status/expiry; a stopped scheduler cannot extend an existing CPBX permit. The
lifespan retry worker resumes durable due work and stops before Redis closes.
Existing remote runtimes retain the existing polling/cancellation limitations.
No arbitrary background UUID activation is allowed; approved background leases
need their own explicit trusted integration.

## Startup, HTTP and WebSocket

`main.py` passes the trusted Genesis security factory into initialize_app. With
GENESIS_SESSIONS_ENABLED absent/false, the existing unbound defaults remain.
Set it true only after the maintenance/provisioning gates above. An optional
GENESIS_SECURITY_POLICY_FILE loads a server-owned JSON deployment snapshot:

```json
{
  "approvals": [{"tenant_id": "tenant", "agent_id": "agent", "owner_user_id": "owner", "enabled": true}],
  "grants": [{"tenant_id": "tenant", "agent_id": "agent", "invoke": ["chat.context"]}],
  "trusted_lines": []
}
```

Omitting the file gives empty approvals/grants. Loading never calls install_policy
or creates sessions. Explicitly provision each tenant using the shared
`container.agentverse.utu_authority()` and its existing policy CAS protocol before
activation. Other workers must load the identical bundle. Do not copy demo grants
into deployment without reviewing the actual registered capability providers.

New controls use `Authorization: Bearer <original provisioned token>`:

- GET `/api/v2/agentverse/session` verifies current authority.
- POST `/api/v2/agentverse/session/logout` returns 204 only after durable UTU
  invalidation; 503 means unacknowledged, keep credentials and retry the same ID.
- WS `/api/v2/agentverse/session/ws` accepts header authentication and ping/pong,
  and rechecks expiry/revocation during idle time and after receives. It accepts
  no query-string token. Browser callers need a bearer-forwarding server proxy.

`authenticated_genesis(connection)` is the domain context bridge for future V2
lifecycle route handlers. Wrap the whole handler/lifecycle call. The session-bound
lifecycle routes above expose V2 activation without migrating V1 agent messages.

The client logout route supports GENESIS_LOGOUT_REQUIRED=true and an explicit
server-owned GENESIS_LOGOUT_URL pointing at the backend POST endpoint. It awaits
204 before clearing the cookie; failures return 503 and retain the cookie. Leave
this flag off for V1. Enable it together with the session-aware login/token
integration, ensuring getApiAccessToken returns the exact provisioned token.
The local development integration above now supplies that guarantee. Entra token
refresh still requires its own fresh-sid issuance and trusted provisioning adapter.

## Verification

Use a **disposable Redis 6** URL, never the live authority database:

```sh
UTU_TEST_REDIS_URL=redis://disposable-redis:6379/0 python -m pytest \
  src/domains/agentverse/agents/lifecycle/tests \
  src/domains/agentverse/ncsc/tests \
  src/base/decorators/middleware/tests/test_ws_session_based.py \
  src/domains/agentverse/tests/websockets/test_ws_route_teardown.py
```

Fixtures create unique namespaces and delete only their own records. New tests
exercise real JWT verification, cross-tenant/user/agent/session rejection, copied
sid rejection, expiry with the scheduler stopped, durable replay after UTU
failure, Redis loss, membership fencing, retained root/nested CPBX clients,
lifecycle cleanup, startup default deny and HTTP/WS controls. Run client logout
checks in caos-client with `npx vitest run src/app/api/auth/logout/route.test.ts`.

Validated in isolated containers: Genesis/CPBX and the shared WS middleware tests
pass. The legacy V1 route teardown suite has 12 failures (missing historical
_dispatch/_identify/presence interfaces and unauthenticated test fixtures); the
same 12 failures reproduce against the untouched source. They were left scoped
out of this security change.

## Verification results for this local integration

- 152 focused backend tests passed (Genesis lifecycle/session, Redis revocation,
  CPBX/NCSC, shared V1 WebSocket middleware), using a disposable Redis 6 container.
- 10 client tests passed (fresh JWT sid, stable token/cookie, V1 mode and logout).
- TypeScript `tsc --noEmit` passed. Targeted Python lint passed; client lint has
  no errors and two existing Entra non-null assertion warnings in login.
- Live browser HTTP sequence and composed retained-client/permit/Anubis check
  passed. Redis restart preserved revoked-token denial and idempotent logout.
- V1 DTO/teardown run: 9 passed, 12 legacy WebSocket teardown failures. These
  match the previously documented untouched-source failures; V1 teardown
  implementation was not changed by this integration.
- Broader chat-tool tests: 3 passed; the existing standard execution-envelope
  test fails because ChatToolV2 inherits unimplemented BaseTool._run requiring
  socket_emitter. Genesis uses the real, verified provide_context adapter.

Remaining limitations: Entra session-aware provisioning, autonomous V2 cognition,
production recovery/ACL/TLS operations, and those legacy test failures are outside
this local development integration. Local access tokens expire after 15 minutes;
use a new login rather than refreshing/reusing a sid.

## Files changed in the local integration continuation

API checkout (`/Users/guidoguidos/Development/caos/caos-api`):

- `docker-compose.yml`, `docker-compose.redis-secure.yml`, `.env.example`: secure
  default Redis, compatible override, loopback host port and setup variables.
- `config/genesis.local.json`: Adam-only approval and exact context grant.
- `src/domains/agentverse/dependencies/genesis_local.py`: development gates,
  V1-compatible private Adam seed, trusted policy validation and observed-version CAS.
- `src/domains/agentverse/dependencies/genesis_bootstrap.py`: opt-in local startup.
- `src/domains/agentverse/security/revocation.py`: read current policy for trusted CAS.
- `src/domains/agentverse/interfaces/api/v2/genesis_sessions.py`: local enrollment
  bridge using verified JWT plus fixed server-owned membership and ownership.
- `src/domains/agentverse/interfaces/api/v2/genesis_agents.py`: session-bound
  lifecycle/status/signal HTTP adapters.
- `src/domains/agentverse/tools/chat_tool_v2/synaptic.py`: use the actual supported
  context-provider arguments (text/history).
- `src/domains/agentverse/agents/lifecycle/tests/test_genesis_local.py` and
  `test_session_authority.py`: local gates/CAS/schema coverage and opt-in isolation.
- `scripts/verify_genesis_local.py`: repeatable composed lifecycle verification.
- `docs/GENESIS_SESSION_INTEGRATION.md`: live settings, commands and results.

Client checkout (`/Users/guidoguidos/Development/caos/caos-client`):

- `src/shared/auth/local/local-token.ts`: signed tenant and fresh UUID sid.
- `src/app/api/auth/login/route.ts`: local login, trusted backend enrollment and cookie.
- `src/shared/auth/lib/session.ts`: cookie requirement and access-token verification
  for enabled local Genesis; V1 local fallback remains opt-out.
- `src/shared/auth/lib/get-api-token.ts`, `src/shared/auth/auth.types.ts`: retain
  and reuse the exact provisioned bearer instead of signing on each call.
- `src/shared/auth/local/local-genesis.test.ts`, `vitest.config.ts`: real JWT/cookie
  regression checks and test-only server-module resolution.
- `.env.example`: local session/logout and authenticated Redis configuration.

Private API `.env` and client `.env.local` were coordinated without printing or
committing secrets. The existing backend-acknowledged client logout implementation
was enabled. Existing unrelated worktree changes were preserved. No Git commit,
release or production change was made. The disposable test container was removed.
