# Genesis Draft Review: Scoped Authority, Review Envelope and Human Approval

**Status:** implemented and closed after verification (G3.3-S + G3.3), uncommitted, on
`feature/ops-v2` over `828321f`.
**Audience:** engineers changing UTU, CPBX, the validation gateway or Agent creation.
**Scope:** how an authenticated human obtains an authoritative review of a
`caos.agent_creation_draft` and records an explicit approval of it. Creation itself
(G3.4) is **not** implemented; an approval grants nothing.

Accepted architectural decisions (G3.3-S review, 2026-10-30):

1. `UTUAuthority.attenuate()` stays a public, trusted in-process method. It is not
   reachable from any HTTP route, synaptic signal or Agent/Tool handle, and it can
   only narrow authority.
2. Gateway eligibility stays an explicit tenant/user/source-Agent binding for the
   local rollout; it is not generalized.
3. JWT role freshness is a documented limitation of the session model (below).
4. Client-handle cancellation and permit revocation remain distinct mechanisms.

## 1. Scoped (attenuated) authority

Source: `src/domains/agentverse/security/capability_scope.py`, `security/utu.py`
(`attenuate`, `require_invoke`), `ncsc/cpbx.py` (`bind`, `_request`),
`security/revocation.py` (`ExecutionPermit`).

- `CapabilityScope(operation, entry, bindings)` is a frozen restriction of exact
  `CapabilityBinding(signal, provider, version)` values. It is stored on the existing
  `ExecutionPermit` (`scope`, `binding_chain`); there is no new credential, grant or
  wire format. Permits are never serialized or persisted.
- **Where authority is established:** UTU issues the permit
  (`resolve_agent_principal`); `attenuate()` returns a copy with a scope. It first
  runs `check_permit` (revoked, expired, superseded or origin-mismatched permits
  cannot be attenuated), requires every scoped signal to already be a root grant of
  the principal, and only accepts a subset of an existing scope.
- **Where it is checked:** on every dispatch, both UTU (`require_invoke`) and CPBX
  (`_request`) enforce it. Effective authority is the intersection of the principal's
  root grants, trusted lines and the scope. The root call must be the scope's entry;
  nested calls must be scoped bindings *and* pass the existing trusted-line policy.
- **Where it is inherited:** CPBX gives each handler a child client whose permit
  carries the extended `binding_chain`. Rebinding a child permit
  (`SynapticExchange.bind`) restores that lineage, so it can never restart as a root
  call or drop the scope.
- **Where it is revoked:** unchanged. A scoped permit is an `ExecutionPermit`, so
  session revocation, token expiry, membership fences and policy transitions
  supersede it exactly like an unscoped one, including for rebound child clients.
- **Dispatch paths:** `SynapticExchange._request` is the only caller of a synaptic
  handler. `operations/admission.py` resolves capabilities and records provenance but
  never executes a handler; it still applies `UTUAuthority.require_invoke`, so a
  scope's entry restriction holds there too.
- **Cancellation vs revocation:** `AuthorizedSynapticClient.revoke()` closes that
  client's execution gate only. A rebound client has its own gate. Permit revocation
  (UTU) is what invalidates authority everywhere.

## 2. Exact-version dispatch

`AuthorizedSynapticClient.request(..., version=...)` pins a provider version. A
missing binding or a different registered version raises `KeyError` (HTTP 503 at the
gateway); a different provider is denied. Within a scope an omitted version resolves
to the single scoped version. Unpinned, unscoped internal requests are unchanged.
`Synapse.provider_version` and `entry_operation` carry the resolved identity to the
handler and its logs. The gateway always requests `data.validation@1.0.0`.

## 3. Trusted resource requester

`RedisAgentSessionAuthority.resource_requester` (via `UTUAuthority.resource_requester`)
returns a `User` built from the verified bearer bound to the current request: tenant,
subject, `roles` and `scp` are parsed exactly as V1's JWT middleware does, and must
match the permit's tenant, user and session. Nothing comes from the draft payload, and
the Genesis executor is never substituted. Bootstrap leases have no roles. Missing or
mismatched context fails closed. Group visibility therefore matches the requester's
authorized catalogs.

**Limitation (accepted):** there is no server-side membership store. Roles are as
fresh as the signed token; a role change takes effect after token reissue or session
invalidation.

## 4. Validation gateway and review envelope

Source: `src/domains/agentverse/security/validation_gateway.py`; route
`POST /api/v2/agentverse/agents/{source_agent_id}/creation-draft/validate`
(`interfaces/api/v2/genesis_agents.py`). Binding: `validation_gateways` in
`config/genesis.local.json`.

Flow: live human session → reviewed gateway binding for (tenant, user, source Agent)
→ the binding's executor resolved through UTU → `attenuate(VALIDATION_SCOPE)` →
source Agent readable by the trusted requester (read-only; a sleeping Enki is not
awakened) → `data.validation@1.0.0` through CPBX → in-process `AgentComposer`.
Authority is re-checked after each step. The gateway holds only a definition *read*
function; it has no Store, grant, approval-provisioning or lifecycle handle.

The response is `caos.agent_creation_review` v1:

| Field | Meaning |
| --- | --- |
| `validation` | The `ValidationResult` with `resolved` withheld and messages generalized |
| `composition` | `compatible`, issues, and `preview`: an allowlisted projection of the composed candidate (no backend selectors, DNA rule or V1 route) |
| `eligibility` | `eligible` and reason codes (validation, composition, `requester.cannot_create`) |
| `review_digest` | Present only when eligible (see below) |
| `validated_at`, `expires_at` | Review time and a 10-minute client freshness window |
| `authorizes` | Always `{creation: false, activation: false}` |

**Review digest:** SHA-256 of an internal snapshot containing the tenant, the
requester (subject, sorted roles and scopes), source Agent, executor, exact capability,
draft contract, normalized input, historical `normalized_digest`, the **complete**
composed candidate, resolved references with content fingerprints of every dependency
(type, role, personality, tools, rule packs, workflows, workflow tools), the UTU policy
revision and the V2 composition policy. It contains no time, so an unchanged draft under
unchanged authority reproduces it. Revocation counters are not policy and are excluded.
The snapshot is never returned to clients. `normalized_digest` is unchanged.

## 5. Human approval

Source: `src/domains/agentverse/agents/creation_review/` (`models.py`,
`repository.py`, `service.py`); routes `POST .../creation-draft/approvals` and
`POST .../creation-draft/approvals/{approval_id}/revoke`.

The request (`caos.agent_creation_review_approval` v1) carries the exact draft, the
expected `review_digest`, `decision: "approve"`, `confirmed: true` and an
`idempotency_key`. The service:

1. Re-reviews the exact draft through the gateway (source authorization included).
2. Applies the existing creator policy to the trusted requester: `Role.USER`,
   `agent.write` through the existing authz permission check, and
   `agent_access.can_create`.
3. Rejects reuse of the key for another request (409), an ineligible review (409) and
   a digest mismatch (409, `review.stale`).
4. Inserts a **pending** `DraftReviewApproval` with a stable ID derived from tenant,
   subject and key, the immutable snapshot (stored as canonical JSON and re-verified
   against the digest on every load) and the decision-time UTU provenance.
5. Appends mandatory Seshat evidence (`agent.creation_review.approved`), built only
   from the stored record, so retries from a new session are identical.
6. Re-checks current authority, then compare-and-sets the record to **approved**.

A pending record is never effective, so every partial failure is recovered by
repeating the request with the same key (503 responses carry `retryable: true`).
Concurrent same-key requests converge on one record and one evidence record.

### Expiration

`expires_at` is fixed when the pending approval is first recorded (`created_at` + 24
hours). Effective state is `approved` only while `now < expires_at`, then `expired`.
Expiry is checked again immediately before the approval takes effect, after audit and
authority checks, and the record invariant `approved_at < expires_at` makes a late
approval unrepresentable. A pending approval past its expiry never takes effect; the
human must review again. The review envelope's 10-minute `expires_at` is a client
freshness window; the server never trusts a review's age because every approval
re-validates the exact draft.

### Audit attribution

Seshat records are attributed only from verified UTU provenance:

| Event | Actor (`principal_id` / `user_id`) | Authorization |
| --- | --- | --- |
| `agent.creation_review.approved` | Gateway executor that performed the reviewed validation / the approving human | The decision-time permit (`origin: session`), stored with the pending record so a recovered approval records identical evidence |
| `agent.creation_review.revoked` | Same executor / the revoking human (always the approver) | The **revoking** session's current permit (`DraftReviewApprovalService.revoke` → `AgentDraftValidationGateway.attribute`), stored once on the record |

Revocation takes effect before its evidence: the state changes first, so an audit
outage or missing authority can never keep an approval usable. If the revoking session
has no current gateway authority, the approval is still revoked and the request returns
a retryable 503 until evidence can be attributed and recorded.

### Effective-approval evidence

`DraftReviewApprovalService.effective(tenant_id, subject_id, approval_id)` is the only
check a later step (G3.4) may accept. The approval record is authoritative; Seshat
evidence is required but not sufficient. It returns the record only when it belongs to
the subject, is `approved` and unexpired, its `approval_event_id` resolves to an
`agent.creation_review.approved` record whose subject, target, metadata and
authorization exactly match the stored approval, and no revocation evidence exists.
Consequently an "approved" Seshat record for a still-pending approval (evidence
committed, state transition lost) is **not** effective, nor is an approved record whose
evidence is missing. `effective()` has no HTTP route. An approval outlives the session
that made it; G3.4 must still re-verify current authority and the digest at creation.

No Agent Store write, grant provisioning, Khepri activation or LLM call occurs.

## 6. Client

`caos-client`: `src/app/v2/forms/[agentId]/` (`useCreationReview.ts`,
`CreationReviewPanel.tsx`, `DraftWorkspace.tsx`), `src/shared/lib/enki/review.ts`,
and a server-side proxy `src/shared/auth/lib/agent-creation-review.ts` under
`/api/caos/v2/agents/{agentId}/creation-review` (requires `GENESIS_AGENTS_URL`). The
browser never holds the bearer. Any edit marks a review stale, superseded responses
are aborted and dropped, expiry disables approval, and approval retries reuse the
digest's idempotency key. Create Agent remains disabled.

## Verification (2026-10-30)

Run in the API container with disposable Redis 6 and `caos_test_*` Mongo databases
(`UTU_TEST_REDIS_URL=… OPS2_MONGO_INTEGRATION=1 python -m pytest src`): 2353 passed,
5 failed, 9 skipped (G3.3 closure run). The 5 failures are pre-existing in
`src/base/events/v2/tests/test_root_signing_key_wiring.py` (the test stubs a database
container without `mongodb_repository`). Focused suites:
`ncsc/tests/test_scoped_authority.py`,
`agents/lifecycle/tests/test_scoped_requester.py`,
`tests/agents/test_validation_gateway*.py`, `tests/agents/test_creation_review*.py`,
`tests/audit/test_caos_log.py`, `tests/tools/test_data_validation_tool.py`.
Client: `npx vitest run src/app/v2/forms src/shared/lib/enki src/shared/auth/lib`
passed (44 tests).
