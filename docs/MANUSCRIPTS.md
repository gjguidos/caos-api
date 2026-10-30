# Manuscripts: Definitions, Runtime, Governed Capability and Tool (MANUSCRIPT-0.1/0.1A/0.1B)

**Status:** implemented, uncommitted, on `feature/ops-v2` over `828321f`; MANUSCRIPT-0.1B
(first-class Tool registration, §9) awaiting architectural review. **Catalog:** an optional,
global/public Tool descriptor; **execution:** only Enki, only
through the six approved, scoped `manuscript.session.*@1.0.0` signals (§6); no HTTP
route or public session API. Enki's conversation loop does not call them yet. No LLM, reasoning, catalog
capability or Ptah integration. **Audience:** engineers building interviews, Enki's turn
plan (ENKI-1.0-B), CATALOG-0.1 or REASONING-0.1.
**Design:** [ENKI-1.0 §3](ENKI_1_0_ARCHITECTURE.md) (approved with conditions,
[ADR-0003](architecture/decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md)).
Source: `src/domains/agentverse/manuscripts/`.

A Manuscript is a versioned, deterministic interview protocol. It decides what must be
collected, in which order, and when the interview is complete. It never decides what to
say (that is the Soul's expression), never interprets free text (REASONING-0.1), and
never writes drafts, approves, creates or activates anything.

## 1. Definition contract (`caos.manuscript` v1, `models.py`)

| Element | Contract |
| --- | --- |
| Identity | `id` (`^[a-z][a-z0-9_]{0,63}$`) and semantic `version` (`MAJOR.MINOR.PATCH`); key `id@version` |
| Artifact | Typed output contract; v1 supports exactly `caos.agent_creation_draft` v1 (`resource_type: agent`) |
| Fields | Up to 64 typed fields: `text` (length bounds, optional regex), `enum` (options), `boolean`, `text_list`, `catalog_ref` / `catalog_ref_list` (catalog kind `agent`, `role`, `personality`, `tool`, `workflow` or `rulepack`; `max_items`); optional `target` JSON pointer into `/creation/…` (none = interview-only context, e.g. purpose); `shape: tool_refs` maps Tool keys to `{key, config: {}}` |
| Stages | Up to 32 ordered stages: `collects` (each field collected by exactly one stage), `required` ⊆ `collects`, optional `completion` predicate, ordered `transitions` (`to`, `when`), optional `guidance` anchor. A stage that collects fields must require something or declare a completion predicate (it would otherwise complete before its question is asked) |
| Flow | `initial_stage`, `final_stage` (no transitions), every stage reachable; Manuscript `completion` predicate |
| Inputs | `prefill`: fields a session may start with (e.g. from the human's current draft) |
| Dependencies | `catalog_dependencies` must equal the catalog kinds fields use; `handoff`: a declared, never-invoked capability (`signal`, `version`, `purpose`) |
| Guidance | Optional Markdown (≤ 50 000 chars): expression material for the Soul and humans, never parsed or executed, no authority |
| Strictness | Unknown keys, unknown operators, malformed regexes and invalid references are rejected at build time |

**Predicate language** (closed): `always`, `present`, `equals` (scalar, type-checked
against the field; enum values must be options), `in` (scalar text fields; enum values
must be options), `catalog_contains` (catalog fields only; membership is established when
the answer is accepted, so evaluation needs no I/O), `all`, `any`, `not`. Bounds checked
at build time: depth ≤ 8, ≤ 64 nodes, ≤ 16 children per `all`/`any`. No expressions,
code, imports, templates or executable Markdown.

**Fingerprint:** SHA-256 of the canonical machine definition (everything except
guidance) plus the SHA-256 of the guidance text. Deterministic; any change to stages,
fields, predicates or guidance changes it.

## 2. Registry (`registry.py`)

`ManuscriptRegistry(Registry[ManuscriptDefinition])` reuses AOSA's registry stores,
metadata and catalog (`@register_registry`), specialized like `SynapticRegistry`: exact
`id@version` keys (generic canonicalization would turn `id@1.0.0` into `id100`),
idempotent refresh of identical content, `ManuscriptConflict` for different content
under an existing key, and `freeze()` after trusted bootstrap. **Published versions are
pinned:** `PUBLISHED_FINGERPRINTS` records each published version's fingerprint and
registration refuses anything else, so published content can never change silently.
`resolve(key, fingerprint)` returns only the exact registered content (a different
fingerprint is `ManuscriptUnavailable`). `outlines()` is the client-safe projection (id,
version, title, fingerprint, stage outline; no guidance).

Bootstrap (`dependencies/genesis_bootstrap.py`) registers the built-ins
(`definitions/__init__.py`) once into `manuscript_registry_instance`, freezes it and
builds `app.state.manuscripts` (in-process `ManuscriptService`).

## 3. Sessions (`sessions.py`, `repository.py`)

`coll_manuscript_sessions`, one document per session (`_id = session_id`), independent
of drafts. Fields: `session_id` (uuid5 of tenant, owner, Agent and idempotency key),
`tenant_id`, `owner_subject_id`, `source_agent_id`, pinned `manuscript {id, version,
fingerprint}`, optional `draft_id` (for the future handoff), `start_digest`, `revision`,
`status` (`active`, `completed`, `restarted`), `stage`, ordered `completed_stages`,
`answers` (`{field: {value, source: human|agent|prefill, revision}}`), `artifact` and
`artifact_digest` (once completed), `restarted_from` / `restarted_to`, `applied` (the last
32 request keys with digests), timestamps and `state_digest` (SHA-256 over all other
fields, verified on every load). No conversations, prompts, permits or credentials.

**Concurrency and recovery.** Every transition replaces the whole document only from its
exact previous revision and state digest (compare-and-set); concurrent transitions from
one revision apply exactly once and the others get `session.revision_conflict` with the
current revision. A request key that already applied (same digest) replays the stored
state, so a retry after a lost acknowledgement never applies twice; a reused key with
different content is `idempotency.conflict`. A crash leaves the previous or the next
complete state. Restart inserts the deterministic successor first, then closes the
predecessor; repeating an interrupted restart completes it.

**Integrity and staleness.** A session whose pinned `id@version` + fingerprint is not
registered is `manuscript.unavailable`; a tampered document (digest mismatch) or one whose
stage, completed stages or answers do not fit its pinned definition is `session.corrupt`.

## 4. Runtime (`service.py`)

`ManuscriptService` operations take a verified `ManuscriptActor` (`tenant_id`,
`subject_id`, `agent_id`, `kind`, trusted `requester`); `ManuscriptActor.from_permit`
builds one from an Agent's verified, session-origin UTU permit (bootstrap and revoked
permits refused; the requester comes from UTU's trusted resource requester).

| Operation | Behavior |
| --- | --- |
| `start(actor, "id@version", idempotency_key, draft_id?, prefill?)` | Pin the exact definition and fingerprint; validate prefill like answers (rejected codes returned); advance through already satisfied stages; idempotent per key (different start content under the key is `idempotency.conflict`). `prefill_from_creation` maps draft `creation` content to prefill fields (still validated) |
| `status(actor, session_id)` | Stage, title, guidance anchor, completed stages, answers, missing required fields, allowed transitions, `ready_to_complete`, artifact; `authorizes` all false |
| `answer(…, expected_revision, answers, request_key)` | Typed validation of answers for the current **or later** stages (non-linear clarification); catalog fields resolved against the requester's catalog; answers to completed stages are `stage.completed` (use `correct`); `None` clears an optional answer. Then deterministic progression: each satisfied stage completes and the first matching transition is followed, each stage at most once per call. Outcome `accepted` / `needs_clarification` / `invalid` with per-field codes and the new stage's missing fields; nothing acceptable spends no revision |
| `correct(…, stage, answers?)` | Reopen a completed (or the current) stage: it and every stage completed after it become incomplete (answers kept), corrections apply (fields of that stage only), progression resumes |
| `restart(actor, session_id, request_key)` | A new active session on the same pinned Manuscript; the old one becomes `restarted`, read-only, keeping any artifact |
| `complete(…)` | Requires the final stage completed, every required field present and the Manuscript completion predicate; emits the artifact (validated against `AgentDraftCreateRequest`) and closes the session read-only |

Answer codes: `field.unknown`, `field.not_in_stage`, `stage.completed`, `answer.type`,
`answer.option`, `answer.too_short`, `answer.too_long`, `answer.invalid`,
`answer.duplicate`, `answer.too_many`, `reference.unavailable` (unknown and unreadable
are indistinguishable).

**Catalog** (`catalog.py`): `RegistryManuscriptCatalog` reuses the `data.validation` read
policy (`RegistryService.get_info_for_client`, `WorkflowService.get_workflow`) for the
requester. A read decision only; CATALOG-0.1 may replace the port.

## 5. `agent_creation_interview@1.0.0` (published)

`definitions/agent_creation_interview.py` and `.md` (guidance). Fingerprint
`4e04558f…4260b38` (pinned).

| Stage | Collects (required in bold) | Completion | Next |
| --- | --- | --- | --- |
| `purpose` | **purpose** (text 10–2000, context only) | required | identity |
| `identity` | **name** (Agent name pattern, ≤ 100) | required | composition |
| `composition` | **agent_type**, **role**, **soul** (catalog `agent`, `role`, `personality`) | all three `catalog_contains` | capabilities |
| `capabilities` | tools (Tool refs), workflows, rule_packs (≤ 20 each), **capabilities_reviewed** | `capabilities_reviewed = true` | configuration |
| `configuration` | **prompt** (10–20000), **visibility** (`private`/`public`) | required | review |
| `review` | **review_ready** | `review_ready = true` | — |

Manuscript completion: name, `catalog_contains` type/role/soul, prompt, visibility in
options, `review_ready = true`. The artifact is a `caos.agent_creation_draft` v1 envelope
whose `creation` holds name, type, role_name, personality_key, tools (`{key, config: {}}`),
workflows, rule_pack_ids, prompt and visibility; it validates as `AgentDraftCreateRequest`
and as a full `AgentCreationInput`, and the unchanged G3.3 gateway reviews it as eligible
(tested). `review_ready` means "ready for server review", never approval.

**Handoff (declared, not invoked):** `agent.draft.propose@1.0.0`. The future turn plan
(ENKI-1.0-B) submits `artifact.creation` as the proposal `changes` against the human's
draft (`draft_id`, current base revision) through `submit_proposal`; the human then
accepts fields (DRAFT-0.2A) and reviews/approves the exact revision (DRAFT-0.2B).

## 6. Authorization boundaries

- MANUSCRIPT-0.1 added no authority; MANUSCRIPT-0.1A added exactly the approved Enki
  grants below. No trusted line; no HTTP route or public session-management API.
- No LLM, Store, draft-write, proposal, approval, creation, Genesis or Khepri handle in
  the runtime or provider (tested); Manuscripts write only their own session collection.
- A definition's `handoff` must be in `ALLOWED_HANDOFFS` (`agent.draft.propose@1.0.0`)
  and is metadata only: it registers, grants and invokes nothing.
- Catalog references are accepted only if readable by the human requester.
- Sessions are isolated by tenant, owner and source Agent (others are `session.not_found`).
- No Seshat events: session transitions have no external effect (the taxonomy is for
  consequential decisions). Proposal acceptance, approval and creation keep their own
  evidence.

### Governed capability (MANUSCRIPT-0.1A: approved, implemented)

Source: `manuscripts/capability.py`; registration in `dependencies/genesis_bootstrap.py`
only when the reviewed policy grants a Manuscript signal.

| Item | Contract |
| --- | --- |
| Signals | `manuscript.session.start`, `.status`, `.answer`, `.correct`, `.restart`, `.complete`, each exactly `1.0.0`, provider `system.manuscript`, registered at bootstrap in the frozen `SynapticRegistry` (six distinct handlers; a conflicting provider is refused); Nataraja declares and runtime-confirms all six |
| Grants | Enki (`…0004`) only, in `config/genesis.local.json`; pinned in `genesis_local.py` (`_ENKI_MANUSCRIPT_GRANTS`); **no trusted line** |
| Scope | One `CapabilityScope(signal, binding, {binding})` per signal (`SCOPES[op]`). The provider requires that exact scope, `binding_chain[-1]`, `entry_operation`, signal, target, `provider_version == "1.0.0"`, tenant and source equal to the permit, and `origin == "session"`. Another Manuscript signal's scope, an unscoped permit (including the generic `POST /agents/{id}/signals/{signal}` route) or a bootstrap permit is denied |
| Authority | Independently, per call: `check_permit` (live session, not revoked or expired) before work and before the result; current principal re-resolved (policy versions unchanged) and the signal's root grant re-checked; the human from UTU's trusted resource requester (`ManuscriptActor.from_permit`) |
| Visibility | A Manuscript whose artifact is `caos.agent_creation_draft` is usable only by a source Agent holding a reviewed validation-gateway binding for that tenant and human (existing policy; Enki locally); rechecked on every call, including for existing sessions |
| References | A `session_id` is never authority: sessions load only for the verified tenant, human and source Agent (others are `session.not_found`), against their exactly pinned Manuscript |
| Commands | Strict and identity-free (`caos.manuscript_session.<op>` v1): `start {manuscript, idempotency_key, draft_id?, prefill?}`, `status {session_id}`, `answer {session_id, expected_revision, answers, request_key}`, `correct {session_id, expected_revision, stage, answers?, request_key}`, `restart {session_id, request_key}`, `complete {session_id, expected_revision, request_key}`; any identity, owner, grant or unknown field is `manuscript.invalid_request` |
| Results | `caos.manuscript_session_result`: `ok`, the session projection (`authorizes` all false), the answer outcome and rejected prefill; failures carry only `code`, `retryable`, `current_revision` or `missing` |
| Caller | Trusted in-process `invoke_manuscript(authority, exchange, principal, op, command)` narrows the verified principal to exactly one signal, binds, requests the exact version, revokes and drains (for ENKI-1.0-B's turn plan) |

**Version immutability (2026-10-30).** `agent_creation_interview@1.0.0` was never
committed, persisted or used by a session before its correction: the registry is
in-memory only, no database on the local server held Manuscript collections, Redis held
no Manuscript keys, and no route or caller existed while the uncorrected fingerprint
(`944c588d…`) briefly lived in uncommitted source and the dev API's memory. The published
1.0.0 is therefore `4e04558f…` (unchanged by MANUSCRIPT-0.1A).

## 7. Verification (2026-10-30)

MANUSCRIPT-0.1A:

- `manuscripts/tests/test_manuscript_capability.py` (34): all six signals drive a full
  interview (start, answer, correct, status, complete, restart) under their own scopes;
  each signal refuses every other signal's scope (30 pairs); unscoped, bootstrap, wrong
  version, target, source, tenant or signal refused before any work; revoked, changed and
  ungranted authority denied; identity and authority fields in commands refused; another
  human's or another Agent's session ID is `session.not_found`; visibility without a
  gateway binding; exact version pinning; request-key replay and 6 concurrent answers (one
  applies, others `session.revision_conflict` with the current revision); exact
  registration and conflicting providers; arbitrary handoffs rejected; no exchange or
  draft/proposal/Store/Genesis/Khepri handle, completion writes only sessions.
- `manuscripts/tests/test_manuscript_capability_integration.py` (1; reviewed local policy,
  disposable Mongo and Redis, 3 runs): only Enki holds each signal and no trusted line
  exists; registered provider and version; Enki completes an interview through all six
  signals via `invoke_manuscript` (invented role rejected, replay applied once,
  correction, completion, restart); CPBX refuses all 30 cross-scope invocations; Genesis
  and JOSHUA cannot narrow to any scope; the generic signal route is denied; no Agent,
  approval, creation, log, draft or proposal change; after logout Enki is denied.
- Exact-grant pins updated in `test_genesis_initiation_integration.py` and
  `test_recursive_genesis_integration.py`.
- Full backend suite with integrations: 2623 passed, 9 skipped, the 5 inherited
  signing-key failures. Ruff and formatting clean; mypy only the inherited Motor stub
  warning in source.
- Runtime (read-only Nataraja `inspect`): the reloaded API installed policy revision
  `f79a003f46ff4ec4c414ab7166c11575a9744c542a9b2e1033db5af97e5b7212` (checked-in policy
  file digest `813a83ee…`); installed, checked-in and runtime versions match; all six
  `manuscript.session.*` providers runtime-confirmed as `system.manuscript@1.0.0`; no
  findings; 3 Agents; no Manuscript or draft collections in live `caos`.

MANUSCRIPT-0.1:

- `manuscripts/tests/test_manuscript_registry.py` (28): published definition and pin,
  deterministic fingerprints (guidance included), exact/idempotent/immutable/frozen
  registration, exact and fingerprint resolution, 22 malformed definitions (unknown
  operators, untyped predicates, bad references, unreachable stages, silent optional
  stages, catalog dependency mismatch, bad regex, extra keys), predicate bounds, pure
  typed evaluation, inert guidance.
- `manuscripts/tests/test_manuscript_runtime.py` (20): full interview and artifact
  (draft contract and `AgentCreationInput`), determinism, partial and volunteered answers,
  invalid answers with codes and no revision, requester-scoped references, corrections,
  completion requirements, retry/idempotency, a write lost after commit, 8 concurrent
  transitions (one applies), restart (incl. completed sessions) and interrupted restart,
  read-only completed sessions, tenant/owner/Agent isolation, validated prefill from draft
  content, stale and fingerprint-drifted pins, tampered and broken sessions, bounded
  progression on cyclic definitions, actor construction from permits, no foreign
  collections or handles, and the real catalog adapter against the validator's fixtures.
- `manuscripts/tests/test_manuscript_integration.py` (3; disposable Mongo and Redis, 3
  runs): 12 concurrent transitions on real MongoDB (one applies), continuation across
  service instances, completion, tamper detection, interrupted restart recovery; over the
  local application: bootstrap-registered frozen registry, actor from Enki's verified UTU
  permit, invented role rejected by the real catalog, full interview, G3.3 review of the
  artifact eligible, no Agent/approval/creation/log/runtime/draft/proposal change.
- Full backend suite with integrations: 2588 passed, 9 skipped, 5 inherited signing-key
  failures. Ruff and formatting clean; mypy only the inherited Motor stub warning in
  source.
- Live: no policy change (`3e2ea22f…`), Nataraja passed with no findings, 3 Agents, no
  Manuscript or draft collections in `caos`, no leftover test databases.

## 8. Known limitations and planned work

- Enki's turn plan does not call the signals yet (ENKI-1.0-B); there is no human-facing
  route (the form and Ptah would need a separately reviewed surface).
- Free-text interpretation (REASONING-0.1), the governed catalog capability
  (CATALOG-0.1), Enki's turn plan (ENKI-1.0-B) and Ptah's `interview_progress` /
  `stage_panel` contributions are planned.
- Tool configuration is not interviewed (Tools are attached with `config: {}`); groups
  (`whitelisted_groups`) and GUI components are not collected by 1.0.0.
- Reserved Agent names are left to G3.3 validation.
- `applied` remembers the last 32 request keys per session; older keys are no longer
  replay-protected (their revision checks still prevent double application).
- Sessions have no retention policy.

## 9. First-class Manuscript Tool (MANUSCRIPT-0.1B)

**Implemented, uncommitted; awaiting architectural review.** Source:
`src/domains/agentverse/tools/manuscript_tool/{__init__,spec}.py`.

These are three different registrations, none of which authorizes execution:

| Registration | Identity | Responsibility |
| --- | --- | --- |
| `ToolRegistry` | `manuscript`, Tool version `1.0.0` | Optional Agent-configured deterministic Tool; trusted specification and configuration contract |
| `ManuscriptRegistry` | e.g. `agent_creation_interview@1.0.0` + fingerprint | Immutable published interview resource, resolved through the specialized exact-key API |
| `SynapticRegistry` | Six `manuscript.session.*@1.0.0`, provider `system.manuscript` | Existing governed operation handlers; exact capability bindings |

Tool, capability and resource versions are independent. No action version is inferred
from the Tool version. `SynapticCapabilitySpec` now supports optional `version`,
`provider`, `input_contract`, `output_contract`; omitted fields stay absent in legacy
serialized declarations. Contract references label the existing command/result contracts,
not a new schema registry or runtime-import mechanism.

The registered Tool declares all six existing actions, each `version="1.0.0"`,
`provider="system.manuscript"`, `access="granted"`, no trusted lines. The older provider's
`access="system"` annotation remains unchanged: both are descriptive, neither grants
anything or determines Tool scope. The Tool is `scope="agent"`, `tool_class="deterministic"`,
not a core/default/system attachment. No Agent definition, Role, Soul or policy changes.

### Configuration

The existing `AgentToolRef` remains `{key, config}`; no persisted executable or action list:

```json
{
  "key": "manuscript",
  "config": {"default_manuscript": "agent_creation_interview@1.0.0"}
}
```

`ManuscriptToolConfig` is strict, frozen and extra-forbidden. Its only field is a string
`default_manuscript` (default above; maximum 100 characters), matching
`^[a-z][a-z0-9_]{0,63}@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`.
No `latest`, identity, provider, grants, scopes, trusted lines or arbitrary control flags.
This validates reference syntax only: existence, fingerprint and requester visibility are
still checked by the existing runtime/provider. Future multiple-reference configuration
can extend this Tool-owned schema without replacing `AgentToolRef`.

### Execution and trusted startup

`ManuscriptTool.execute()` and `_run()` deny direct execution; the descriptor has **no V1
route**, so V1 ToolManager also refuses routing even if attached. `execution_mode` is
descriptive, not the denial mechanism.

The trusted `ManuscriptTool.invoke(authority, exchange, principal, op, command)` adapter
delegates to unchanged `invoke_manuscript`: UTU narrows to exactly one operation, CPBX
dispatches the exact binding, and the scoped client is revoked/drained. Only a missing
start-command `manuscript` is filled from configuration; explicit references and existing
session commands are unchanged. The façade constructs no actor, engine, repository,
permit or privileged service. All §6 checks remain authoritative.

`configure_genesis_transport` imports/registers the descriptor independently of grants.
When policy enables Manuscript execution, `validate_manuscript_bindings` checks the actual
registered Tool metadata against all six registered CPBX bindings **before local
initialization/provisioning**. Missing/duplicate/extra actions, unpinned or conflicting
providers/versions, changed contracts/access and trusted-line declarations fail startup.
An enabled policy without the gateway/provider also fails. With no Manuscript grants,
the descriptor may be discoverable while providers remain unregistered; no grant is added.
This is a bounded Manuscript check, not a generic V2 assigned-Tool executor.

### Requester-aware catalog

The descriptor has `ownership="global"`, `exposure="public"` and no system/builtin tags;
`system.manuscript` remains only the executable provider identity. Existing
`RegistryService.list_for_client/get_info_for_client` and manifest projection expose
descriptive identity, tags and the one safe `ui.admin.fields`/`agent_config_schema` field.
The schema's default identifies the published built-in, never a private definition body.
Operative signals/provider bindings, raw config, private definitions, credentials and
session contents are not projected. No public projection rules were relaxed.
Discovery, attachment and configuration never imply that an Agent can execute the Tool.

### Verification and environment

Tests: `tools/manuscript_tool/tests/test_manuscript_tool.py` and
`test_manuscript_startup.py`; the existing real UTU/CPBX integration now exercises the
registered façade with an `AgentToolRef`. The focused regression run (2026-10-30) passed
**214 tests**, including all Manuscript tests and real Mongo/Redis integrations, Tool
registration/projection and production-style startup wiring. This covers exact pins,
CAS/concurrency/replay, cross-scope/revoked/tenant/requester denials and no draft,
proposal, Agent-creation or activation effects from Manuscript operations. Fixtures may
provision disposable test identities only. Existing serializer/deprecation warnings remain.

Full backend run: **2672 passed, 9 skipped, 5 failed**; the five failures are the inherited
`src/base/events/v2/tests/test_root_signing_key_wiring.py` fixture failures (stub database
container lacks `mongodb_repository`). Command inside the disposable test container:
`python -m pytest -p no:cacheprovider --disable-warnings -q`, with
`OPS2_MONGO_INTEGRATION=1`, disposable `DB_URI`/`UTU_TEST_REDIS_URL`, a test-only signing
key and `AUTHZ_ENABLED=false` (no external user-management service in this environment;
UTU/CPBX authorization stays active). An initial run without that test-only external
authz setting also had 11 missing-`AUTHZ_URL` failures; no application policy was changed.
Scoped Ruff formatting passes; lint passes excluding four confirmed pre-existing
`tools/spec.py` findings (`EXE002`, two `BLE001`, `S110`). Scoped mypy passes with
`--explicit-package-bases --follow-imports=silent --disable-error-code no-untyped-def`;
without that last switch it reports eight pre-existing untyped validators in `tools/spec.py`.

Tests run in a separate Docker container on an isolated network with disposable MongoDB
and Redis, source/config mounted read-only; no live database access. With explicit user
approval, the reload-enabled development API was paused **before source edits** and is
left paused for review. Live deployment of the new descriptor was not verified; resuming
the API would reload code and run normal bootstrap, so is a separate operator decision.

## 10. Future authoring lifecycle (planned, not implemented)

- **Manuscript Designer:** an authoring surface for structured definitions and inert
  guidance; no direct registration or execution authority.
- **Validation:** reuse typed definition/predicate checks, add reviewed authoring
  diagnostics and dependency/version checks; validation neither publishes nor grants.
- **Isolated Sandbox:** test candidates against disposable sessions, catalogs and
  identities; no live drafts, privileged Store, proposal handoff or production authority.
- **Governed publication:** separately approved publication of a new exact version and
  fingerprint through trusted registration; never mutate a published definition or
  unfreeze a running registry from client input. Publication does not provision grants.

No Designer, validation capability, Sandbox, publication API, new signal/grant/trusted
line, CATALOG-0.1, REASONING-0.1, Ptah or client changes are implemented by 0.1B.
