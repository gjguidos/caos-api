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
| DEF-1 | No governed revision exists, so governed definitions cannot be corrected at all | Proposed: ADR-0005 milestones REV-1 to REV-5, then ENKI-COMP-0.1 |
| DEF-2 | A V1 Workflow edit changes the behavior of governed Agents that reference it | Workflow governance is separate; G3.3 binds Workflow versions at review. Proposed separate milestone **WF-GOV-0.1** (ADR-0005 §7) |
| DEF-3 | Direct database or operator access is outside this boundary | Operational control |
| DEF-4 | The admin ingest route still reveals Agent existence to admins (`Agent '<id>' not found`) and checks no per-Agent ownership | Pre-existing; unchanged. Proposed separate milestone **V1-INGEST-SEC-0.1** |
| DEF-5 | Governance is marker-based: a governed definition written without any marker would be treated as V1 | No such writer exists; `store.create` always records both markers. REV-1 added revision records but no new marker (no projection writes yet); an explicit `governance` marker remains proposed for REV-4 |
| DEF-6 | `coll_agents` has no unique `id` index; `find_by_id` returns an arbitrary holder if duplicates exist | Found in REV-1. Store's scope index blocks same-scope duplicates only. Nataraja now reports any duplicate as a `conflicted` baseline. A unique `id` index needs a separate, approved migration |

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
