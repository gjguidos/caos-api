# Agent Definition Governance (AGENT-DEF-SEC-0.1)

**Status:** implemented and accepted (2026-10-31); committed in `c1719b9` on
`feature/ops-v2`. Governed revision (§5–§9) is implemented in source and verified with
disposable infrastructure only: the revision-application grant is not installed, and no
live Agent has been revised. Security finding: [AGENT-DEF-SEC-0.1](SECURITY_FINDINGS.md).
**Audience:** engineers changing Agent persistence, legacy routes, Genesis creation or the
Conversation Tool, and the reviewers of ENKI-COMP-0.1.

A **governed definition** is an Agent definition created through a governed path. It can
change only through that governed lifecycle (validation → review → human approval →
Store). No legacy route may change or delete it. There is **no governed revision path
yet**, so governed definitions are currently immutable after creation. The revision
architecture is
[ADR-0005](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)
(approved direction with corrections). Implemented so far: REV-1 (immutable history and
read-only baseline verification, §5) and REV-2 (revision drafts and digest-bound reviews,
§6). Approval and application are not implemented, so governed definitions remain
immutable.

## 1. Which definitions are governed

`runtime/compatibility.py` `is_governed_definition(document)` is true for a definition
with any of:

| Marker | Set by | Population |
| --- | --- | --- |
| `runtime_version` other than `v1` (today `v2`; unknown values fail closed) | `store.create` and the provisioned Genesis path | Enki, Thoth, Nabu and every Genesis/G3.4-created Agent |
| A `store` creation record | `store.create` (`provisioned` and `approved`) | Same |
| `genesis_local_seed: true` | The historical trusted local seed | Earlier local seeds |

All three markers are server-managed. `AgentUpdateRequest` rejects `runtime_version` and
`genesis_local_seed`, and the legacy store adapter's field allowlist drops `store` and
`runtime_version` (tested). No legacy write can therefore mark or unmark a definition.

## 2. Legacy mutation paths and enforcement

| Path | Reaches | Fields it could change on a governed definition before | Now |
| --- | --- | --- | --- |
| `PATCH /api/v1/agents/id/{id}` (`AgentService.update_agent`) | `DBService.update_agent` | `type`, `role_name`, `personality_key` (Soul), `tools` (attachments and configuration), `prompt`, `workflows`, `rule_pack_ids`, the backend selectors (`llm_type`, …), `name`, `system_name`, `metadata`, `collections`, `visibility`, `whitelisted_groups` | `409 AGENT_DEFINITION_GOVERNED` |
| `DELETE /api/v1/agents/id/{id}` (`AgentService.delete_agent`) | `DBService.delete_chat_agent` | The whole definition (it could then be re-provisioned under a pinned identity) | `409 AGENT_DEFINITION_GOVERNED` |
| `POST /api/v1/agents/{id}/collection/{name}/ingest-pdf` (admin) | `DBService.add_collection_to_agent` → `update_agent` | `collections` (it also created a collection and vectors first) | `409`, refused before any collection, vector or definition write |
| Store adapter `DBService.update_agent` / `delete_chat_agent` (any caller) | `coll_agents` | As above | Refuses governed definitions (defense in depth) |

**Not mutable through these paths, before or after:** `gui_components`, `runtime_version`,
`store`, `genesis_local_seed`, identity, tenant, `creator`, ownership and exposure.

**Enforcement details:**
- **Ordering:** the check runs after the existing read and write checks. A requester who
  cannot see or write the Agent gets exactly the error they got before (`404` / `403`,
  identical for governed and V1 definitions), so the new error reveals nothing new.
- **Error:** `409` with code `AGENT_DEFINITION_GOVERNED`, a fixed message and no Agent data.
- **No bypass:** there is no alternate update path and no exception for Enki, Genesis,
  JOSHUA or superadmins. `store.create` remains insert-only.
- **V1 behavior is unchanged:** V1 definitions keep update, delete and collection linking.

**Not changed:** Workflow definitions referenced by an Agent are still edited through
their own V1 Workflow routes (§4).

## 3. Creation and Conversation composition

- **New definitions converse only through an explicit attachment.** Conversation
  compatibility is now an allowlist of immutable identities
  ([Conversation Tool §5](CONVERSATION_TOOL.md)). A new Agent cannot obtain implicit
  conversation by selecting `chat/general`, `chat/thoth` or `form/enki`.
- **Creation outcomes are unchanged.** `V2CompositionPolicy.creation_compositions`
  (`chat/general`, `chat/thoth`, `form/enki`) states which compositions may be created
  without the Tool and which receive the default Conversation presentation. This keeps the
  reviewed composer output (and the pinned Enki/Thoth candidates and manifest) unchanged.
  It is presentation and creation shape only, never conversation availability.
- **Consequence:** a new `chat/general` Agent created without the Tool presents a
  Conversation component but every turn returns `composition_unavailable`. Nataraja
  reports this as `conversation.presented_without_composition`. Attaching the Tool needs
  governed revision (ENKI-COMP-0.1) or inclusion in the creation draft.

## 4. Residual risks

| ID | Risk | Notes |
| --- | --- | --- |
| DEF-1 | No governed revision existed, so governed definitions could not be corrected | **Resolved in source** (2026-11-03): REV-1 to REV-5, committed in `c1719b9`. **Not enabled live:** the `store.revise.approved` grant is not installed (Stage 2, [Implementation Status](architecture/IMPLEMENTATION_STATUS.md)) |
| DEF-2 | A V1 Workflow edit changes the behavior of governed Agents that reference it | Workflow governance is separate; G3.3 binds Workflow versions at review. Proposed separate milestone **WF-GOV-0.1** (ADR-0005 §7) |
| DEF-3 | Direct database or operator access is outside this boundary | Operational control |
| DEF-4 | The admin ingest route still reveals Agent existence to admins (`Agent '<id>' not found`) and checks no per-Agent ownership | Pre-existing; unchanged. Proposed separate milestone **V1-INGEST-SEC-0.1** |
| DEF-5 | Governance is marker-based: a governed definition written without any marker would be treated as V1 | **Accepted risk** (2026-11-03). No such writer exists, and `store.create` always records both markers. REV-4 adds the `definition_revision` projection marker, but `is_governed_definition` (`runtime/compatibility.py`) still keys on `runtime_version`, `store` and `genesis_local_seed`. No explicit governance marker is implemented, and an applied revision keeps its `store` marker |
| DEF-6 | `coll_agents` has no unique `id` index; `find_by_id` returns an arbitrary holder if duplicates exist | **Open.** Found in REV-1. Store's scope index blocks same-scope duplicates only. **Fails closed:** revision application refuses `identity_not_unique`, revision targets require one holder, and Nataraja reports a duplicate as `conflicted`. A unique `id` index needs a separate, approved migration, which depends on COSMOS-1 ([Security Findings](SECURITY_FINDINGS.md)) |

## 5. Definition history and baseline verification (REV-1)

Implemented, committed (`c1719b9`), accepted
([ADR-0005 §12–§13](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)).

- **History:** `caos.agent_definition_revision` v1 records are insert-only,
  tenant-qualified, chained by digest and Agent-neutral (`agents/definition_revisions/`).
  - A record is a **selected successor**: not an approval, not proof that `coll_agents`
    was updated, and not a reconciled runtime.
  - Nothing writes records in REV-1 outside tests.
- **Baseline verification:** read-only and pure. It compares the current definition
  with its `store.create` evidence using that record's own rule (`store.agent_definition@1`).

  | Status | Meaning | Can a future revision start? |
  | --- | --- | --- |
  | `verified` | The creation digest is reproduced; single holder; provenance matches | Yes (after REV-2 to REV-4) |
  | `unverified` | Evidence exists but equality cannot be established (e.g. model evolution, unattributed fields) | No; needs a future governed adoption decision |
  | `conflicted` | Evidence contradicts the baseline (duplicates, provenance mismatch, a post-creation write) | No; needs investigation |
  | `unavailable` | No creation evidence (V1, seeds), malformed records or an unreadable store | No |

- **Nataraja:** `inspect` shows one `definition_baseline` resource per governed
  definition, so Enki, Thoth and Nabu can be checked read-only before any migration.
  Inspection writes no baseline record.

## 6. Revision authoring and review (REV-2)

Implemented, committed (`c1719b9`), accepted
([ADR-0005 §14](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)).

| Step | Implemented | Not implemented |
| --- | --- | --- |
| Candidate | Owner-authored complete candidates; immutable draft revisions with CAS | — |
| Validation | Scoped `data.validation` and composition; revision-specific checks | — |
| Review | Immutable, digest-bound, 10-minute review; fail-closed currency | — |
| Approval | Implemented in REV-3 (§7): immutable, digest-bound, withdrawable, Seshat evidence | — |
| Application | Implemented in source (REV-4, §8): Genesis-only `store.revise.approved` under `agent.definition.revise`; grant not declared in the checked-in policy | Live use |
| Runtime | — | REV-5 |

- **Who:** only the Agent's `creator`, through a reviewed `revision_gateways` binding,
  with the existing `agent.write` route policy.
- **What:** only V2 governed definitions whose baseline is `verified` (or whose stored
  history matches the projection).
- **No authority:** no Agent, System Citizen or generic signal route can author, review
  or apply a revision, and nothing in REV-2 writes `coll_agents`.

**Open deployment concerns:** Cosmos DB vCore verification, and DEF-6 (no unique `id`
index; live Enki, Thoth and Nabu have no duplicates).

## 7. Human approval (REV-3)

Implemented, committed (`c1719b9`), accepted
([ADR-0005 §15](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)).

- **Who:** the Agent's owner approves one exact, current review (`review_id` and
  `review_digest`) in a live human session, under the existing route policy.
- **One decision per review:** approvals are deterministic per human per review, so
  repeats and concurrent requests converge.
- **What makes it effective:** the approved record, its exactly matching Seshat
  evidence, no withdrawal, no expiry (24 h), and the same predecessor, gateway binding
  and UTU policy revision as at decision time.
- **Withdrawal:** by the approver; it takes effect before its evidence is recorded.
- **No authority:** an approval never applies a revision, writes `coll_agents`, grants a
  capability or activates an Agent. Application is REV-4 and is not implemented.

## 8. Application (REV-4)

Implemented in source and disposable tests, committed (`c1719b9`), conditionally accepted
and corrected in REV-5; the grant is not installed
([ADR-0005 §16](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)).

- **Request:** the owner asks to apply an effective approval
  (`POST /agents/{id}/definition-revisions/approvals/{approval_id}/application`; status
  via `GET`).
- **Execution:** Genesis applies it through `store.revise.approved` under
  `agent.definition.revise`. No human, Agent or generic route can call the signal.
- **Claim:** one application per predecessor. A withdrawal committed before the claim
  wins; after the claim it is refused.
- **Result:** the projection becomes the selected immutable revision via one
  compare-and-set. Creation evidence and identity are preserved; runtimes are not
  reconciled (REV-5).
- **Live use needs:** the revise grant in the checked-in policy (a UTU policy revision),
  an API restart and an explicit migration approval.

## 9. Runtime consistency (REV-5)

Implemented in source and disposable tests, committed (`c1719b9`), accepted at the
source/test level, subject to deployment verification
([ADR-0005 §17](architecture/decisions/ADR-0005-governed-agent-definition-revision.md)).

- **Turn binding:** every V2 turn is bound at admission to the authoritative definition
  (canonical digest plus applied revision). A change before execution yields a
  retryable `definition_changed`, retryable only as a new turn; journaled outcomes are
  never re-executed.
- **REV-4 corrections:** consumption must precede approval expiry; consumption binds the
  exact application; the predecessor is checked before selection; the projection
  compare-and-set is fenced on every revisable field.
- **Nataraja** inspects the whole history (baseline, chain, selected/applied
  projection, incomplete or unevidenced applications, direct mutation, duplicates)
  read-only.
- **Runtime instances** hold no definitions, so nothing needs restarting.

## 10. Revision recovery runbook (REV-REC-1)

This runbook describes existing capabilities only. UTU remains the authority. Nothing
here adds an endpoint, a grant or an operator override. Governed revision is not enabled
live until Stage 2 installs the `store.revise.approved` grant.

### 10.1 Lifecycle and authority

Every step needs an authenticated, live Genesis session of the owning human. Routes are
under `/api/v2/agentverse/agents/{agent_id}/definition-revisions/`
(`interfaces/api/v2/agent_definition_revisions.py`). Each call re-checks:
- the policy's `revision_gateways` binding (tenant and subject to the Genesis executor);
- the target: governed, created by the requester, writable, and a single holder.

| Step | Route | What happens | Evidence |
| --- | --- | --- | --- |
| Baseline | `GET baseline` | Read-only baseline status and predecessor | — |
| Draft | `POST drafts`, `PATCH drafts/{id}` | Revision draft. The predecessor is the chain head, or the verified baseline. Refused if the chain head does not match the projection (`projection.mismatch`) | — |
| Review | `POST drafts/{id}/reviews` | `data.validation` under Genesis's attenuated validation scope, then composition. Binds the UTU policy revision and the gateway fingerprint; expires | Review record |
| Approval | `POST approvals`, `POST approvals/{id}/withdrawal` | Human approval of an exact review (24 h). Withdrawal is possible until consumption | Seshat `approved` / `withdrawn` |
| Application | `POST approvals/{id}/application`, `GET …/application` | Described below | Seshat `selected`, `applied` |

**Application** (`agents/definition_revisions/application.py`):
1. A claim, one per predecessor.
2. Approval consumption. This is the point of no withdrawal.
3. An OPS V2 operation with a 60 s lease, executing the Genesis-only
   `store.revise.approved@1.0.0` under the `agent.definition.revise` scope. The provider
   exists only when the installed policy grants it.
4. Inside the provider: re-verification, then the immutable successor revision record
   (plus the baseline record if needed), then one projection compare-and-set against the
   exact predecessor.
5. Receipts, then the operation succeeds and the record is completed.

Record states are `claimed`, `selected`, `applied`, `completed` and `failed`. Every Store
step is idempotent, and claim and application IDs are deterministic.

### 10.2 Interruption states

| # | Durable state | Recovery |
| --- | --- | --- |
| 1 | Claimed, approval not consumed | Repeat the request. An expired or withdrawn approval releases the claim |
| 2 | Approval consumed, no operation | Repeat the request |
| 3 | Operation running, nothing selected | Repeat after the 60 s lease (`in_progress` until then) |
| 4 | Successor recorded, projection unchanged | Repeat; the identical successor is reused, then applied |
| 5 | Projection applied, receipts missing | Repeat; the applied projection is recognized and receipts are written |
| 6 | Record `selected` or `applied`, evidence partly written | Repeat; receipts are idempotent |
| 7 | Operation succeeded, record not completed | Repeat; the record is completed from authoritative state |
| **8** | **Held claim, terminal disagreement:** record `selected` with a selected digest, OPS operation terminally `FAILED`. Entered when the provider returns a non-retryable `predecessor_changed` after selection | **Unsupported, escalate.** Every retry returns "Operation and claim disagree; inspect before retrying". Other approvals for the predecessor get `predecessor_claimed`, and new drafts get `projection.mismatch` |
| **9** | **Released claim, orphaned successor:** record `failed` with no selected digest, while an unapplied successor revision exists. Entered when a later attempt hits `identity_not_unique`, `identity_mismatch`, `approval_mismatch`, `review_unavailable`, `candidate_mismatch`, `predecessor_missing` or `successor_conflict` | **Unsupported, escalate.** New drafts get `projection.mismatch`, and Nataraja reports `revision.selected_unapplied` |

States 8 and 9 need an out-of-band change to `coll_agents` (DEF-3), a duplicate holder
(DEF-6) or corruption. Crashes and lost connections alone lead only to states 1-7
(inferred from code). In both, the runtime keeps serving a consistent projection.

### 10.3 Procedures

**Supported now:**
1. **Inspect:**
   - Nataraja `inspect` (read-only): the `definition_baseline` codes
     `application.incomplete`, `application.missing_evidence`,
     `revision.selected_unapplied` and `projection.*`, and `incomplete_applications`;
   - `GET approvals/{id}` and `GET approvals/{id}/application`;
   - a read-only lookup of `operations_v2` by `operation_id = application_id`.
2. **Freeze:**
   - no policy or grant change while any application is unfinished (without the grant
     no retry can run);
   - avoid API restarts between steps (each costs one lease).
3. **States 1-7:** the owner, with a live session, repeats the identical
   `POST approvals/{id}/application`. On `in_progress`, wait out the lease and repeat.
   Preconditions: the same approval, the grant and gateway still installed, and the
   target still a single governed holder.
4. **Before consumption:** withdraw the approval, or let it expire. A `claimed`-only
   claim is released on the next request.
5. **Verify:** Nataraja shows `verified` with history `applied`, the application is
   `completed`, and Seshat holds both `selected` and `applied`.

**States 8 and 9 (escalate only):**
1. Stop.
2. Preserve the evidence: the application record, revision records, OPS operation and
   Seshat chain.
3. Find the cause: an out-of-band writer (DEF-3), a duplicate (DEF-6) or corruption.
4. Escalate for an architectural decision.

**Prohibited:** undocumented database repair, claim deletion, selection reversal,
projection edits, and creating, editing or deleting revision records or Seshat
evidence. A database restore needs explicit approval, and would leave the append-only
evidence inconsistent with the restored data.

**Future capability (not implemented):** a governed "abandon or supersede selection"
operation, recorded in Seshat, for states 8 and 9.
