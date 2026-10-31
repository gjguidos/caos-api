# Security Findings Register

Findings discovered during CAOS development, with their disclosure path, correction and
evidence. Each entry is labelled **corrected (uncommitted)**, **corrected (committed)**,
**open** or **accepted**.
Corrections are reviewed like any other milestone; this page grants nothing and is not
runtime policy.

---

## ACTCONSENT-1 — Legacy compatibility consented to every activity contract

**Status:** corrected, committed in `c1719b9` (2026-10-31, MIG-FIX-0.1); accepted.
Found during the MIG-PRE-0.1 readiness review.
**Severity:** low (latent). No production route or handler starts an Activity Context,
and consent never replaced authorization: the `conversation.v2` owner still required
`chat.response` and UTU conversation access, and Manuscript sessions their own grants.
- **Gap:** REV-5 consent returned "unrestricted" for reviewed legacy compatibility, so
  Thoth and Nabu (and any conversation-owned contract registered later) would have
  passed the definition check. An unwired declaration source, or an owner kind without a
  consent method, also meant "allowed".
- **Correction:**
  - legacy entries declare exact, validated activities (Enki: the creation interview;
    Thoth and Nabu: none);
  - `declared_activities` always returns an explicit set of contracts that resolve for
    `conversation.v2`;
  - a missing source, a non-set answer or an owner kind without consent denies new
    generations (`start` / `switch`);
  - existing contexts stay readable, closable, replayable and reconcilable.
- **Evidence:** `activities/tests/test_activity_consent.py` (14 tests, disposable
  MongoDB), plus the updated REV-5 and Conversation Tool assertions.

---

## REVGW-1 — Revision gateway changes were not bound into reviews

**Status:** corrected, committed in `c1719b9` (2026-10-31, REV-3); accepted.
Found during the REV-3 boundary review.
**Severity:** low–moderate integrity gap.
- **Gap:** `revision_gateways` bindings are policy configuration but not part of the UTU
  `policy_version` digest. A binding changed at the next start (for example a different
  executor) would not supersede existing permits, and a REV-2 review recorded only its
  executor ID, so an old review could still read as `current`.
- **Scope:** no approval or application existed, so nothing could act on a stale review.
- **Correction:**
  - reviews bind `gateway_fingerprint`, and approvals bind it plus the UTU policy
    revision;
  - review currency and approval effectiveness fail closed (`stale_authority`) when the
    binding fingerprint, executor or policy revision differs;
  - a removed binding denies outright.
- **Evidence:** `test_changed_authority_policy_or_gateway_fails_closed` (disposable).
- **Related, not changed:** G3.3 `validation_gateways` and `creation_gateways` have the
  same property. Creation approvals bind the executor ID and re-run the gateway at
  creation; a fingerprint there is a separate decision.

---

## AGENT-DEF-SEC-0.1 — Legacy routes could change governed V2 Agent definitions

**Status:** corrected, committed in `c1719b9` (2026-10-31, `feature/ops-v2`); accepted. Found during CONVERSATION-0.1A.
**Severity:** moderate integrity bypass. The legacy routes let an Agent's creator (or a
superadmin or group admin with write access) change or delete a governed V2 definition
without validation, review, approval or Seshat evidence. No grant, signal or policy could
be obtained, but composition, Role, Soul, prompt and Tool configuration could.

### Bypass paths (before the correction)

1. `PATCH /api/v1/agents/id/{id}` → `DBService.update_agent`. Its allowlist accepted
   `type`, `role_name`, `personality_key`, `tools`, `prompt`, `workflows`,
   `rule_pack_ids`, backend selectors, `name`, `metadata`, `collections`, `visibility`
   and `whitelisted_groups`, with no runtime check.
2. `DELETE /api/v1/agents/id/{id}` → `DBService.delete_chat_agent`. It could delete Enki,
   Thoth or Nabu; a pinned slot could then be re-provisioned.
3. `POST /api/v1/agents/{id}/collection/{name}/ingest-pdf` (admin) →
   `add_collection_to_agent`. It appended `collections` to any Agent, with no per-Agent
   ownership check.
4. **Related (CONVERSATION-0.1A):** conversation compatibility matched `(type, role)`
   pairs, so changing a role (path 1) or creating a new `chat/general` Agent conferred
   implicit conversation.

**Affected population:** every V2 definition (Enki, Thoth, Nabu and all Genesis/G3.4
creations) and historical trusted seeds. GUI declarations, runtime and identity fields
were never writable through these paths.

### Correction

- **One rule:** `is_governed_definition` / `require_legacy_mutable` in
  `runtime/compatibility.py`.
- **Enforced in:** `AgentService.update_agent` and `delete_agent` (after the existing read
  and write checks), the store adapter `DBService.update_agent` and `delete_chat_agent`,
  and PDF ingestion (before any write).
- **Error:** typed `409 AGENT_DEFINITION_GOVERNED` with no Agent data. Callers who could
  not see or write the Agent still get their unchanged `404` / `403`.
- **No bypass:** no alternate update path and no exception for Enki, Genesis or JOSHUA.
- **Compatibility containment:** conversation compatibility is an allowlist of immutable
  `(tenant, Agent ID)` identities (Enki, Thoth, Nabu), each valid only with its reviewed
  `(type, role)`. New Agents need the explicit Tool.

### Evidence

- `tests/services/test_agent_definition_integrity.py` (52 tests; real disposable Mongo,
  real `DBService`, `AgentService` and V1 routes): every field change, `DELETE`, the store
  adapter, PDF ingestion, the routes' `409`, non-disclosure ordering, marker immutability
  and unchanged V1 behavior.
- `tools/conversation_tool/tests/`: the identity allowlist, new `chat/general`,
  `chat/thoth` and `form/enki` Agents without the Tool, and a runtime test with the
  production allowlist.
- Full backend: 3103 passed, 11 skipped, 5 inherited root-signing failures.

### Remaining (open)

The residual risks DEF-1–DEF-5 are listed in
[Agent Definition Governance §4](AGENT_DEFINITION_GOVERNANCE.md).

---

## VALIDATION-SEC-0.1 — `data.validation` named Tools the requester could not read

**Status:** corrected, uncommitted (2026-10-30, `feature/ops-v2` over `b7c40fb`);
awaiting architectural review. Found during CATALOG-0.1A ([Catalog §6](CATALOG.md)).
**Severity:** low–moderate information disclosure (identifier and count of internal
resources; no data, configuration, grant or execution exposure).

### Disclosure path (before the correction)

1. A Role is client-readable when every operative dependency is valid for the requester.
   RG-02F's `RegistryService._dependencies_valid` deliberately lets **system/internal**
   Tools satisfy a Role's `capabilities.required_tools` without making them readable
   (the client projection filters them out of the Role's own metadata). Unknown,
   foreign-tenant and others' private requirements instead make the Role itself
   unreadable.
2. `AgentDefinitionValidator._composition` (`tools/validation_tool/agent_validator.py`)
   compared the Role's raw required Tools with the requested Tools and emitted
   `composition.required_tool_missing` with message `Role requires tool '<key>'` — for
   every missing key, readable or not, one finding per key.
3. The `data.validation@1.0.0` provider returns the `ValidationResult` unchanged to its
   CPBX caller. Consumers:
   - **G3.3 review envelope** (`security/validation_gateway.py`): already replaced every
     message with a generic text and nulled `resolved` — **not affected**.
   - **Genesis provisioner** and Seshat: forward/record issue **codes** only — **not affected**.
   - **Generic signal route** `POST /api/v2/agentverse/agents/{GENESIS}/signals/data.validation`:
     the owner of the local Genesis System Citizen can awaken it in their session
     (existing behavior) and call its granted `data.validation` with an unscoped permit;
     the HTTP response is the raw result. **Affected.** Reproduced end to end against the
     pre-correction validator: HTTP 200 with `Role requires tool 'analyze'`, where
     `analyze` is an internal Tool the human cannot read.

Exposed: the identifier of each internal Tool a readable Role requires, and (by repetition)
how many there are. Not exposed: Tool metadata, configuration, grants or bindings.

### Correction

- `_composition` now names a Tool only when the requester may READ it, using the existing
  client READ policy (`RegistryService.get_info_for_client`, new helper `_readable`).
  Otherwise the message is `Role requires a tool that is unavailable to this requester`.
  The same rule applies to `composition.rulepacks_insufficient`
  (`A required tool needs more rule packs`) as defense in depth; Workflow-required Tools
  are already readable whenever the Workflow is (RG-02F).
- `_Issues.sorted()` collapses identical findings (same path, code and message), so the
  number of hidden requirements is not observable.
- `ValidationIssue` documents the invariant.

Unchanged: validity, issue **codes** and **paths**, `normalized`, `normalized_digest`,
`resolved`, the G3.3 review digest and envelope, G3.4 creation eligibility, UTU grants,
CPBX bindings, the generic route and Genesis's authority. Detailed diagnostics remain
available only inside trusted boundaries (raw registry metadata through trusted
`RegistryService.get_info`, Nataraja); they are not added to logs or results.

### Security invariants

1. A requester-facing validation message never names a resource the requester cannot READ.
2. Unknown and hidden references produce identical findings (code, path, message), for
   Agent Types, Roles, Souls, Tools, Workflows and RulePacks.
3. Repetition never reveals how many hidden resources produced a finding.
4. Message projection never changes the authoritative decision (validity, codes, paths,
   digests).

### Evidence

Disposable Docker container on an internal network with throwaway MongoDB/Redis; live API
paused; no live database access.

- `tests/tools/test_data_validation_disclosure.py` (13): hidden required Tool unnamed;
  several hidden requirements collapse; readable required Tool still named (alone and
  mixed with a hidden one); unknown, foreign-tenant and private requirements hide the Role
  exactly like a missing Role; hidden vs unknown Soul and Agent Type identical; hidden vs
  unknown Tool (foreign, internal, private), Workflow and RulePack references identical;
  RulePack findings name readable Tools; authoritative outcome and digest unchanged.
  Before the correction 3 of these failed on disclosure.
- `tests/agents/test_validation_disclosure_integration.py` (1): the generic route on an
  awakened Genesis with the real local policy and registries; fails against the
  pre-correction validator (`Role requires tool 'analyze'`), passes after.
- Regressions: focused 1566 passed; full backend 2759 passed, 9 skipped, 5 inherited
  `test_root_signing_key_wiring.py` failures. Ruff check/format pass on changed files;
  one mypy `union-attr` in `agent_validator.py` is inherited (present at `HEAD`).

### Continuity with reasoning (REASONING-0.1 review)

Reasoning tasks may receive validation **codes and paths only**, never `resolved`,
fingerprints or raw `ValidationResult`s; with that rule VS-1/VS-2 are not made more
exploitable by LLM consumers. Recommended independent remediation is recorded in
[REASONING-0.1 §8](REASONING_0_1_ARCHITECTURE.md) (VS-1: attenuate Genesis provisioning to
an explicit scope, then require scoped `data.validation`; VS-2: keep raw fingerprints
inside trusted boundaries; any G3.3 digest change is a separate milestone).

### Remaining related findings (open, not changed here)

| ID | Finding | Notes |
| --- | --- | --- |
| VS-1 | Genesis's `data.validation` is reachable unscoped through the generic signal route by its owner, returning the full `ValidationResult` (including `resolved`) | Unlike Enki's scoped capabilities. `resolved` contains only readable references today. Requiring a scoped permit in the provider needs a review of the Genesis provisioner's client first |
| VS-2 | `resolved.fingerprints` are SHA-256 digests of **raw** registry metadata of readable resources, including fields the client projection omits | Allows confirming a guessed private value; low. Needed for G3.3 content binding, so a change affects review digests |
| VS-3 | A generic `required_tool_missing` (and the Catalog's `required_tool_unavailable`) still reveals that a readable Role has *some* unreadable requirement | Inherent to keeping the authoritative code; no identifier or count |
| VS-4 | The validator reads each Workflow through the client policy, then re-reads raw rows; a concurrent edit between the reads could put an unreadable Tool key into `resolved.workflows[].required_tools` | Narrow race; messages are now guarded |
