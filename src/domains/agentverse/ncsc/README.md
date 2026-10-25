# CPBX authorization

Trusted composition code constructs `SynapticAuthorization`, passes it to
`SynapticExchange(registry=..., authorization=...)`, and binds a verified
`EventPrincipal`. Handlers accept `(synapse, client)` and use that child client
for nested requests. Broker publish/subscribe grants do not grant CPBX access.

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
  does not implement payload/resource authorization or per-user/session grants.
- The external principal resolver must verify user/session ownership before
  binding an agent. The lifecycle connector verifies agent and tenant matching.
- Configuration is snapshotted at construction. Policy reload/revocation and
  cancellation of already running handlers require application lifecycle work.
  Do not distribute serialized execution contexts as proof of authorization.
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
The result is `EventPrincipal(agent_id, tenant_id)`, never Khepri's caller.

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
UUIDs do not currently implement this protocol. The default authority raises a
permission error. Bind the real authority before using Genesis; do not expose
Khepri/connector directly to unauthenticated calls. Authorization of lifecycle
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

The container constructs one shared authorization snapshot for the default
resolver and exchange. `grants_for` exposes immutable CPBX grants; it never
updates policy internals. Empty/missing grants deny activation. Exact signal
authorization still runs in the exchange on every call. Bind trusted inputs
before first resolving services. Custom exchange/resolver overrides remain
supported for trusted integrations and must be supplied separately from
`GenesisSecurityBindings`. V1 routes and their ownership/read rules are unchanged.

Session and persisted definition checks occur on resolution/connection and
caller lifecycle operations, not every call of an already issued client. Session revocation, policy reload and
cancellation of active runtimes require coordinated lifecycle work. Rebuild the
resolver/exchange pair and retire existing clients for a new policy snapshot;
never mutate `_grants` or distribute a serialized context as authorization proof.


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
No new HTTP/WebSocket endpoint is introduced. Shutdown and lost-lease cleanup
use private trusted deactivation so revoked sessions cannot prevent cleanup.

Policy and approvals remain construction snapshots. Session and definition
checks run on resolution and lifecycle operations, not every request from an
already issued CPBX client. Revocation does not automatically stop runtimes or
cancel in-flight handlers. Coordinated policy reload, client retirement and
runtime cancellation remain future lifecycle work; replacing a DI singleton
alone does not revoke references held by existing runtimes.
