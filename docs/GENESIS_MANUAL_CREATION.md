# Genesis Manual Creation: Governed Creation of One Approved Agent

**Status:** implemented, uncommitted (G3.4), on `feature/ops-v2` over `828321f`; awaiting
architectural review. **No live Agent has been created.**
**Audience:** engineers changing Genesis, Store, UTU/CPBX scopes or Agent creation.
**Prerequisite:** [Genesis Draft Review](GENESIS_DRAFT_REVIEW.md) (G3.3-S/G3.3).

Chain: effective human approval → independent creation authorization → Genesis →
Store → **sleeping** Agent. Nothing activates the Agent, provisions grants for it or
calls an LLM.

## 1. Gateway contract

`POST /api/v2/agentverse/agents/{source_agent_id}/creation-draft/create`
(`interfaces/api/v2/genesis_agents.py`), header `Idempotency-Key` (16–128 of
`[A-Za-z0-9_-]`), body `caos.agent_creation_request` v1:

```json
{"contract": "caos.agent_creation_request", "schema_version": 1,
 "approval_id": "…", "expected_review_digest": "<64 hex>"}
```

Nothing else is accepted (`extra="forbid"`): no definition, executor, tenant, grant,
backend or signal. The response is `caos.agent_creation` v1 (`creation_id`, `state`,
`approval_id`, `operation_id`, `agent_id` only when completed, `issue_codes`,
`retryable`, `authorizes: {activation: false}`): **200** for `completed`/`failed`,
**202** for `accepted`/`provisioning` (repeat the same request), **409** for
conflicts and stale/consumed/revoked approvals, **403** for denials, **503**
`retryable: true` when an outcome or its evidence is not yet confirmed.

## 2. Authorization (`agents/creation_review/creation.py`, `AgentCreationService`)

Every request, before any dispatch:

1. Live authenticated human session (`origin: session`); reviewed `creation_gateways`
   binding for (tenant, user, source Agent) in `config/genesis.local.json`.
2. Current trusted requester (UTU) satisfies the existing creator policy: `Role.USER`,
   `agent.write` (authz permission check) and `agent_access.can_create`.
3. The approval belongs to the requester and tenant and matches the source Agent,
   the binding's executor and `expected_review_digest`.
4. `DraftReviewApprovalService.effective()`: approved, unexpired, unrevoked, with
   exactly matching Seshat evidence.
5. The validation gateway re-reviews the approved normalized input: current source
   access, `data.validation@1.0.0`, composition, dependency fingerprints and policy
   must reproduce the approved review digest exactly (`review.stale` otherwise).
6. The executor is resolved through UTU and **attenuated twice**: the existing
   validation-only scope, and `CREATION_SCOPE` (`agent.creation_draft.create`, entry
   and only binding `store.create.approved@1.0.0`). The validation permit never
   executes Store; the creation permit cannot validate, call `store.create`,
   `store.create.provisioned` or any Genesis signal.

### `store.create.approved@1.0.0` (Store, `system.store`)

Registered only when granted (`genesis_bootstrap.py`). It refuses any permit that is
not the creation scope (an unscoped Genesis permit, which now holds the root grant,
is refused by the handler itself) and independently re-verifies at the write:
`effective()` for the permit's tenant and subject, executor, review digest, that the
approval is consumed by this creation, and that the submitted candidate equals the
approved snapshot's. It writes the **approved** candidate, never the submitted one.

**Policy change for review:** the reviewed local policy adds `store.create.approved`
to Genesis's root grants (attenuation can only narrow existing grants). The explicit
local-policy pins accept exactly the G1 set or the G1 set plus this signal
(`dependencies/genesis_local.py`, `agents/genesis/initiation.py`). Enki's grants,
trusted lines (none) and general `store.create` (absent) are unchanged.

## 3. Genesis reuse

`GenesisProvisioner` gains one target type: a request names a policy slot (unchanged;
slot operation IDs are byte-identical) **or** an `ApprovedCreation` (approval,
digest, creation). Approved requests require a separate Store client with the same
verified identity. Genesis independently re-runs validation and composition through
CPBX under the existing OPS V2 managed operation, then writes through
`store.create.approved`; its existing reconcile, lease and Seshat semantics apply.

## 4. Identity, consumption and durability

- **Agent ID:** Store's rule, `UUID(scope_digest(tenant, subject, Genesis,
  "approved:{approval_id}")[:32])`. One approval → one Store idempotency scope → one
  Agent. Database-level uniqueness is the existing unique partial index on
  `store.idempotency_scope` in `coll_agents` (the ID is a function of the scope).
- **Approval consumption:** `DraftReviewApproval.consumed_by` is set once by
  compare-and-set to the creation ID. Retries of that creation proceed; any other
  request is `approval.consumed`.
- **Creation record** (`coll_agent_creation_operations`, `_id` = creation ID derived
  from tenant, subject and idempotency key): requester, tenant, source, executor,
  approval, review digest, request fingerprint, Genesis operation ID, stable target
  Agent ID, decision and completion provenance, Store request digest, evidence IDs,
  state and timestamps. Same key + different request → `idempotency.conflict`.
- **States:** `accepted` (record inserted) → `provisioning` (authorization evidence
  recorded, Genesis dispatched) → `completed` | `failed`. Genesis in-progress,
  interrupted or audit-uncertain outcomes stay `provisioning` (202/503, never
  `failed`); OPS V2 reconciles interrupted attempts on the next request.
- **Completion** requires authoritative Store evidence: the persisted definition at
  the target ID with the expected scope, tenant and creator, and Genesis's
  `genesis.store.completed` evidence with the same request digest.
- **Revocation/expiry:** before Store writes, they make the creation fail (settled to
  `failed` only once no Genesis attempt can still execute); after Store wrote, a
  later revocation never deletes the Agent and never blocks recording completion.

## 5. Seshat evidence

All share `correlation_id = operation_id = ` the Genesis operation, actor = Genesis
executor for the human requester, tenant from UTU provenance:

| Event | Meaning | Causation |
| --- | --- | --- |
| `agent.creation_review.approved` | Human approval (G3.3) | — |
| `agent.creation.authorized` | Creation authorized (requested), before dispatch | approval evidence |
| `genesis.validation.completed`, `genesis.composition.completed` | Genesis re-verification | — |
| `genesis.store.completed` | Provisioned by Store (target Agent ID) | composition |
| `agent.creation.completed` | Completion established from Store evidence | store evidence |

Metadata carries identifiers and digests only (creation, approval, review digest,
source, target, Store request digest, `created`); never the snapshot or credentials.
Authorization and completion provenance are stored on the record once, so retries
from any later session record identical evidence.

## 6. Client

`caos-client`: `src/app/v2/forms/[agentId]/` (`useCreationReview.ts`,
`CreationReviewPanel.tsx`), `src/shared/lib/enki/review.ts`, proxy
`/api/caos/v2/agents/{agentId}/creation-review/create`. Create Agent is enabled only
for a current, approved review and an explicit confirmation; one idempotency key per
approval (kept in `sessionStorage`) is reused for every retry; pending outcomes are
re-checked automatically (bounded) or on demand; the completed summary links to the
Agent. The server remains authoritative.

## 7. Residual risks

- Revocation racing a write that already passed Store's `effective()` check can still
  create the Agent (bounded by the check-to-insert window); it is then an ordinary
  provisioned Agent.
- A creation settled `failed` after revocation while an expired attempt's process is
  still alive cannot be fenced at Store (Store writes are not OPS-fenced).
- Approval/creation expiry relies on each worker's clock.
- The creation key is per browser tab; losing it before any response means a new key
  meets `approval.consumed` (approve a new review instead).

## 8. Proposed first live creation (not executed)

Prerequisites: review of this design; restart the development API to load the
updated policy (startup verifies the existing Enki read-only); `GENESIS_AGENTS_URL`
in `caos-client/.env.local`; an operator token with `agentverse-user` and a group
admin role (or superadmin) and `agent.write` when authz is enabled; a provisioned
local V2 session.

Draft (existing registered type, role and Soul):

```json
{"contract": "caos.agent_creation_draft", "schema_version": 1, "resource_type": "agent",
 "creation": {"name": "Nabu", "type": "chat", "role_name": "general",
  "personality_key": "empty_eva", "visibility": "private",
  "prompt": "You are Nabu, a concise assistant for verifying governed Agent creation in CAOS."}}
```

Rehearsed in disposable infrastructure by
`test_agent_creation_integration.py::test_nabu_acceptance_scenario` (also the
`nabu_acceptance` area of `python -m src.domains.agentverse.nataraja verify`).

Steps: Validate on server → confirm eligibility and preview → approve → create once →
verify one new `coll_agents` document (chat/general/empty_eva, v2, `creator` = the
operator, no runtime instance), the five-event Seshat chain and the completed creation
record; repeat the create (same result, no new Agent). Do not activate it.

## Verification (2026-10-30)

API container, disposable Redis 6 and `caos_test_*` Mongo:
`UTU_TEST_REDIS_URL=… OPS2_MONGO_INTEGRATION=1 python -m pytest src`: 2364 passed,
5 failed (pre-existing `src/base/events/v2/tests/test_root_signing_key_wiring.py`),
9 skipped. Focused: `tests/agents/test_agent_creation.py`,
`tests/agents/test_agent_creation_integration.py`, the G3.3 suites, Genesis
initiation/bootstrap/recursive integration suites. Client:
`npx vitest run src/app/v2/forms src/shared/lib/enki src/shared/auth/lib` (48 passed);
the full client suite has 33 inherited failures in 8 files that import none of this work.
