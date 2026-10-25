# Genesis UTU revocation

UTU owns policy and revocation mutations. CPBX is the enforcement point. V1
remains independent. Bootstrap, session adapters, lifecycle callers, handlers,
and Python internals remain trusted; these objects do not sandbox Python.

## Provisioning and session integration

Use the shared `container.agentverse.utu_authority()` singleton. The Redis store
uses the existing `RedisRepository.eval` abstraction, Redis 6-compatible Lua,
and one durable hash per tenant (one Redis Cluster slot). No pub/sub is needed.
Missing tenant state, unsupported repository adapters, Redis errors and timeouts
deny execution. Records have no TTL and must never be evicted or deleted.

The session provider must implement `RevocableAgentSessionAuthority` and register
the supplied UTU handler through `bind_revocation_handler`. Logout, expiry,
tenant membership removal, and session invalidation must await
`handler(tenant_id, session_id=session_id)` before acknowledging invalidation.
All writers must use this protocol. Expiry needs a durable scheduler or an
expiry-aware adapter that initiates revocation; a process-local timer is not
sufficient. Existing get_session-only providers are deliberately denied for V2
activation. No production session adapter existed in the inspected Genesis path;
that path remains fail closed until the application supplies one. Background
workers never reinterpret a session UUID as proof of an authenticated caller.
Owner lifecycle identity remains separate from the issued agent identity.

Provision each tenant explicitly:

```python
utu = container.agentverse.utu_authority()
version = await utu.install_policy(tenant_id, expected=None, authorization=policy)
```

`expected=None` only creates a previously unprovisioned tenant. For policy/grant
changes use the last returned opaque version as `expected`; CAS detects competing
administrators. Every transition gets a fresh epoch, including A→B→A or a reload
of identical grants. Old clients cannot regain authority. `policy` is the whole
trusted policy bundle, including exact root grants and scoped trusted lines.
Each tenant stores its deployment version. Coordinate bundle transitions for all
tenants that use the bundle; inconsistent workers deny rather than fall back.
Other workers call `utu.reload_policy(policy)` on their existing singleton.
Reload alone grants nothing: the bundle fingerprint must match Redis. Existing
issued permits retain their original epoch and must be replaced by reactivation.
The DI policy provider is bootstrap configuration; UTU owns live policy reloads.

Trusted administrative revocation calls are:

```python
await utu.revoke(tenant_id)                         # whole tenant
await utu.revoke(tenant_id, agent_id=agent_id)      # all agent sessions
await utu.revoke(tenant_id, session_id=session_id) # all agents in session
```

Revocation is idempotent and terminal for that scope/ID. Use new agent/session
IDs for new authority. Removing an agent's grants or delegation uses a policy
transition. Re-enablement is intentionally not implemented. These methods are
trusted control-plane APIs, not CPBX capabilities or payload-driven routes.

## Execution and supervision

Resolution verifies the caller/session, persisted ownership and approval, then
issues a session-bound permit with tenant incarnation and policy epoch. Every
CPBX invocation, including nested invocations and retained child clients, checks
Redis and the current policy before handler entry, and checks the permit again
before returning a result. Payload fields never supply any of this identity.
Static SynapticAuthorization remains available for separately composed local
CPBX callers; Genesis DI always installs UTU enforcement.

Each invocation has an execution gate shared with its children and tracked tasks.
Local revocation closes runtime gates and requests cancellation synchronously
before the durable mutation is awaited. UTU notifies the same lifecycle
coordinator used by Anubis, through its trusted local revocation path; cleanup
bypasses an invalidated caller session, not UTU execution enforcement. Shutdown
is supervised in tracked tasks. The revoke result acknowledges the Redis mutation,
not completed runtime teardown. A timeout/connection failure is indeterminate;
local gates remain closed and the caller must retry the idempotent revoke.

No positive authorization cache is used. Redis checks have a 1 second timeout;
this trades availability and extra Redis round trips (entry and completion) for
prompt stale-client denial. No DB/session-provider lookup is made per CPBX call.
Running operations and idle runtimes poll at most every 0.5 seconds by default.
Remote cancellation detection normally takes up to 0.5 seconds plus a Redis read
(up to 1 second) and event-loop scheduling delay. This is a cooperative bound,
not a real-time guarantee under process suspension or a blocked event loop.
Distributed workers enforce records directly even if notifications are lost.

Activation is checked before construction, connect, start and registry publication.
Construction has an independent lease-renewal/cancellation guard. Pending starts
are tracked before construction; revocation during awaits cancels activation.
Failed stops and cancellation-resistant work retain runtime tracking and lease
renewal. Successful stop precedes registry removal and compare-token lease
release. ManagedAgentV2 drains CPBX tasks for up to 2 seconds; a pending task
makes stop fail and retains ownership. Heartbeats continue during failed stop.
Application lifespan shuts down Genesis before closing Redis.

## Durability and limits

The Compose Redis 6 service now specifies AOF, appendfsync=always, and noeviction
on its existing data volume. **Apply this configuration to the Redis deployment
before enabling Genesis.** This change does not restart the running Redis service.
The integration tests exercise Redis 6 scripting and concurrency, not power-loss
recovery. Do not promote stale asynchronous replicas or restore old snapshots
while accepting traffic: rolled-back revocation state can resurrect old permits.
After disaster recovery fence all old workers/clients and reprovision authority
before accepting traffic. For automatic failover with stronger durability, use
an authority store/deployment that guarantees acknowledged writes are retained.

A boundary admission that wins the race before revocation may already be running.
Cancellation cannot instantly undo committed external effects or unsend a network
request. Handlers and effect adapters must use child CPBX clients for each effect
boundary and cooperate with cancellation. Detached tasks, threads, direct I/O,
blocking Python and hostile handlers are outside this contract; isolate them or
use downstream fencing/idempotency/transactions. Lease expiry, worker pauses and
Redis outages cannot prevent arbitrary old Python from executing. After Redis
loss, gates close and cleanup is requested; ownership renewal may be impossible.
No lease system can guarantee continued physical exclusivity under that partition.

Tests: set `UTU_TEST_REDIS_URL` to a disposable Redis 6 database and run
`python -m pytest src/domains/agentverse/agents/lifecycle/tests src/domains/agentverse/ncsc/tests`.
Tests use unique keys and delete only their own records; they never flush Redis.
