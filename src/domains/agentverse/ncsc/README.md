# CPBX authorization

**Status:** Observed implementation, reconciled against source at
`8cf05979ac22b3131791b7aabc3d34ef20ff90d1`; not proof of deployment or OPS V2.
Unprefixed source paths below are relative to `src/domains/agentverse/`;
paths beginning with `src/` are repository-root-relative. Markdown links are
relative to this document.

Trusted composition code constructs `SynapticAuthorization`, passes it to
`SynapticExchange(registry=..., authorization=...)`, and binds a verified
`EventPrincipal`. Handlers accept `(synapse, client)` and use that child client
for nested requests. Broker publish/subscribe grants do not grant CPBX access.
This describes standalone composition with a bare policy snapshot. Default
Genesis DI instead supplies the shared `UTUAuthority` as the exchange policy,
adding the permit/revocation boundaries described below.

`SynapticGrants(invoke=...)` contains exact root signals and is keyed by
`(tenant_id, principal_id)`. Missing grants deny access. There is no wildcard
matching. Registration alone grants nothing.

`TrustedLine(source, target)` explicitly delegates a downstream capability to
every principal/tenant granted the root. Optional `tenant_id` and `principal_id`
fields narrow that edge. Both specified scopes must match. For example, a grant
for `chat.respond` plus `TrustedLine("chat.respond", "llm.generate")` permits
chat to invoke the LLM while direct LLM invocation remains denied.

Every invocation rechecks the original tenant/principal's root grant and every
edge of the exchange-issued invocation chain. A direct grant for a downstream
signal does not substitute for a trusted line during a nested call. Repeated
signals and chains longer than 64 invocations are denied. Targets must match
the registered provider, including on nested calls. Nested calls retain the
root identity/correlation and use the immediate parent synapse as causation;
payload identity fields and nested correlation overrides cannot change them.

## Trust boundaries

- Bootstrap, `bind`, policy configuration, registry registration, and Python
  context/client internals are trusted. These objects do not sandbox hostile
  Python handlers; isolate untrusted code outside this process.
- Unscoped trusted lines are deliberately global delegation edges. Configure
  tenant/principal scopes where global delegation is inappropriate. This policy
  snapshot does not implement payload/resource authorization or per-user/session
  grants. The default UTU-backed path additionally requires session-bound permits;
  resource access still requires the appropriate handler/service checks.
- The external principal resolver must verify user/session ownership before
  binding an agent. The lifecycle connector verifies agent and tenant matching.
- Bare `SynapticAuthorization` configuration is snapshotted at construction;
  its execution context checks grants on entry, not durable permit revocation.
  The default UTU-backed path implements controlled policy transitions,
  revocation and cooperative invocation cancellation. Do not distribute
  serialized execution contexts as proof of authorization or attribute UTU's
  stronger guarantees to every separately composed exchange.
- Base event signing, broker policies, and V1 paths remain separate. In
  particular the inherited broker policy is keyed by agent ID; this CPBX
  tenant-scoped policy does not extend broker authorization guarantees.


## Genesis principal resolution and trusted bootstrap

`agents/lifecycle/agent_principal_resolver.py` implements the callable resolver
used by UTU and, through UTU, `TrustedNCSCConnector`. It checks session identity,
exact tenant/user/session/agent authorization, a server-approved enabled owner,
and the persisted definition's `id` and `creator` through the real
`DBService.find_by_id` API. Explicit persisted tenant/disable restrictions also
narrow access. Public visibility, group/admin read access, tool lists, payload
identity fields and broker grants never authorize Genesis activation or CPBX.
The resolver identifies the agent, never Khepri's caller. UTU subsequently issues
the permit-bound agent principal used by the default connector/exchange.

Storage gaps are intentional deny boundaries: legacy `Agent` has no tenant or
enablement fields. `AgentActivationApproval` is an explicit trusted configuration
attestation of tenant, owner and enabled state, snapshotted by the resolver.
No approval means deny; legacy creator data alone is insufficient. Provision
approvals only after verifying persisted ownership. UTU owns these decisions
through the deterministic system facade described below.

`AgentSessionAuthority.get_session(session_id)` is an explicit protocol requiring
an authenticated execution context or server-approved background lease. Its
implementation must check current tenant membership, owner, session binding,
expiry/revocation and approved agent activation. A UUID lookup alone is not an
authority. V1 JWT verification, token-hash session keys, and generated WebSocket
UUIDs alone do not implement this protocol. The unbound default raises a
permission error. An implemented `RedisAgentSessionAuthority` in
`security/sessions.py` verifies signed claims and durable server-provisioned
sessions. Genesis composition is opt-in; loading its policy JSON does not itself
provision durable tenant policy/session state. V2 activation also requires the
revocable session protocol, not merely a `get_session()` implementation. See the
[session integration guide](../../../../docs/GENESIS_SESSION_INTEGRATION.md).
Do not expose Khepri/connector directly to unauthenticated calls. Authorization of lifecycle
operations (including status/deactivation and reuse of active instances) remains
a separate caller boundary, not permission to impersonate the agent.

Trusted composition can pass `GenesisSecurityBindings` using the existing
application root:

```python
# All inputs below come from verified server configuration/authority adapters.
security = GenesisSecurityBindings(
    sessions=authenticated_session_authority,
    authorization=SynapticAuthorization(
        grants=approved_tenant_agent_grants,
        trusted_lines=approved_delegation_edges,
    ),
    approvals=approved_tenant_agent_ownership,
)
initialize_app(app, domain_security={"genesis_security": security})
# Or initialize_domains(container, genesis_security=security), or
# extend_container(container, genesis_security=security).
```

The container wires one shared live UTU authority into the default resolver,
exchange and lifecycle; that authority evaluates the currently loaded
`SynapticAuthorization` snapshot. `grants_for` exposes immutable CPBX grants;
it never updates policy internals. Empty/missing grants deny activation. Exact
signal authorization still runs in the exchange on every call. Bind trusted
inputs before first resolving services. Custom exchange/resolver overrides remain
supported for trusted integrations and must be supplied separately from
`GenesisSecurityBindings`. V1 routes and their ownership/read rules are unchanged.

Persisted definition ownership/enablement and trusted approvals are checked at
resolution/connection and caller lifecycle boundaries, not re-read on every
invocation. This does not remove continuing permit/session checks from issued
UTU-backed clients. Use UTU's policy-transition/revocation APIs on the shared
authority rather than mutating `_grants`, replacing DI providers or treating a
serialized context as proof. See the execution lifecycle distinctions below.


## UTU system authority

`security/utu.py` provides `UTUAuthority`, a deterministic CAOS governance
service constructed as a DI singleton. It is not a DB agent, does not use an
LLM and does not receive capability grants of its own. `GenesisSecurityBindings`
continues to supply trusted configuration through the existing application and
domain bootstrap APIs. No bindings means startup succeeds but Genesis denies.

The default connector resolves agent principals through UTU. UTU reuses
`AgentPrincipalResolver` for authenticated session, approval and persisted
ownership verification, and delegates capability decisions to the same
`SynapticAuthorization` snapshot. CPBX remains the local enforcement point on
every root and nested request; broker and V1 policies remain separate.

Public lifecycle methods (and Khepri/Anubis) verify caller authority on every
activation, reuse, status and deactivation. The caller must have a current
session bound to the exact tenant/user/session and allowed agent, an approval
for that owner and matching persisted ownership. Activation additionally
requires enabled approval/definition and agent CPBX grants. Status and stopping
remain available to that verified owner if the agent is disabled or has no
invoke grants. An agent's grants alone never authorize a lifecycle caller.
Missing approvals or session authority deny all caller lifecycle operations.

Custom connector principal resolvers remain trusted integration overrides;
they do not replace UTU's lifecycle verifier. Do not wire the raw lifecycle,
factory, connector, registry or private cleanup helpers to public endpoints.
The facade and private lifecycle helpers are not public APIs. Session transport
routes do exist in `interfaces/api/v2/genesis_sessions.py`; their existence does
not authorize bypassing the trusted connector or lifecycle checks. Shutdown and
lost-lease cleanup use private trusted deactivation so revoked sessions cannot
prevent cleanup.

## Implemented permit and execution lifecycle

Approvals remain trusted construction-time snapshots. Persisted ownership or
enablement changes alone are not automatically re-read as permit revocation on
every CPBX call. In the default UTU-backed path:

- `UTUAuthority.check_permit()` validates the session-bound permit and durable
  tenant/agent/session revocation and policy version. The concrete Redis session
  adapter also checks current durable session activity/expiry and binding.
  Checks run at invocation entry, periodically during active execution and before
  returning; idle managed runtimes also poll permit validity. This is not a fresh
  DB ownership lookup on each request.
- `install_policy()` performs an expected-version durable transition and updates
  the local snapshot. Other workers explicitly `reload_policy()` with the matching
  policy. Reload alone does not provision durable state or make old permits valid:
  saved root/nested clients retain their original permit epoch and gate.
- `revoke()` closes matching local runtime gates and requests cancellation before
  awaiting durable revocation. Other workers detect invalid authority through
  durable checks/polling; this is cooperative, not instant or real-time remote
  termination. A revoke acknowledgement covers the durable mutation, not completed
  runtime teardown. Storage failures deny execution; an indeterminate mutation
  requires the existing idempotent retry protocol.
- Lifecycle cleanup is supervised: pending activation can be cancelled, active
  runtimes are fenced and stopped, and successful stop precedes instance removal
  and lease release. Failed stop/drain retains tracking and ownership rather than
  falsely reporting quiescence or promising eventual cleanup.

Lease renewal depends on storage availability. Retained runtime tracking does
not guarantee physical exclusivity after lease expiry or a Redis/network outage.

Actual implementations: [UTU authority](../security/utu.py),
[durable revocation store](../security/revocation.py),
[session authority](../security/sessions.py),
[lifecycle coordinator](../agents/lifecycle/service.py) and
[managed runtime](../agents/runtime_v2.py). The
[revocation guide](../../../../docs/GENESIS_REVOCATION.md) covers provisioning,
policy transitions, failure handling and distributed limits.

## Cancellation boundaries: invocation, runtime and operation

| Boundary | Implemented behavior | What it does not establish |
| --- | --- | --- |
| Active CPBX invocation | `ExecutionGate.revoke()` fences the shared client gate and requests cancellation of tracked tasks; UTU adds permit polling and a post-handler check that rejects an invalid authorized result | Forced termination, rollback of committed external effects, or cancellation of detached threads/direct I/O |
| Managed agent runtime | `ManagedAgentV2.revoke()` fences its client; `stop()` drains CPBX work. `ExecutionGate.drain()` raises if work remains after its two-second wait; lifecycle retains ownership on failed stop | Immediate completed teardown merely because revocation was acknowledged |
| Existing durable operation record and local runner | `src/base/operations/runner.py` resolves a stored run but `OperationRunner.cancel(run_id)` cancels only its locally tracked task. Its `_run()` records cancellation via `OperationService.cancel()` when `CancelledError` propagates | A CPBX revoke automatically calling operation cancellation, cross-worker durable cancellation orchestration, or implemented OPS V2/Enlil supervision |

Correlation with a run ID is not a cancellation protocol. These UTU/CPBX controls
govern invocation tasks and managed runtimes; they do not by themselves cancel
separately scheduled durable operations. Durable-operation comparison here is
source evidence, not a new distributed lifecycle guarantee. OPS V2 remains planned.

## Behavioral evidence and scope

Focused verification entry points; test existence is not itself an executed result:

- [Revocation tests](../agents/lifecycle/tests/test_utu_revocation.py):
  `test_existing_root_and_saved_nested_client_revoked`,
  `test_policy_cas_stale_workers_and_aba`,
  `test_remote_revocation_cancels_inflight_and_stops_runtime`,
  `test_local_revoke_cancels_runtime_work_before_lease_release`,
  `test_failed_stop_keeps_renewing_ownership`, and
  `test_cancellation_resistant_effect_retains_ownership_until_drained`.
- [Session tests](../agents/lifecycle/tests/test_session_authority.py):
  `test_logout_revokes_existing_nested_clients_and_lifecycle`,
  `test_expiry_fences_saved_client_without_scheduler`, and
  `test_idle_lifecycle_observes_expiry_without_request_context`.
- [CPBX authorization tests](tests/test_authorization.py) and
  [exchange tests](tests/test_cpbx.py): bare-policy grants, targets and nested lineage.

Redis integration tests require `UTU_TEST_REDIS_URL`; use a disposable Redis 6
database rather than interpreting skipped integration tests as passing evidence.
No documentation claim above changes authority, implements OPS V2, or proves a
deployed end-to-end application.
