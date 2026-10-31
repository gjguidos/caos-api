# Governed Resource Catalog (CATALOG-0.1 / 0.1A)

**Status (2026-10-30, `feature/ops-v2` over `b7c40fb`, uncommitted):**

| Part | Status |
| --- | --- |
| **Catalog service** (`ResourceCatalog`, CATALOG-0.1) | Implemented; **accepted** by the architect |
| **Enki Catalog capability** (`catalog.project@1.0.0`, CATALOG-0.1A) | Implemented under the architect's approval; **awaiting review** |
| **Manuscript catalog port** | Implemented; delegates to the service |
| **Ptah-ready presentation metadata** | Implemented as declarative data only |
| Ptah components, Tool Store UI, projection route, media/asset service | **Planned — not implemented** |

**Audience:** engineers building Enki's turn plan (ENKI-1.0-B), REASONING-0.1 and Ptah.
**Design:** [ENKI-1.0 §0.2](ENKI_1_0_ARCHITECTURE.md) item 4,
[Ptah Realm §1, §5–§8](PTAH_REALM_ARCHITECTURE.md),
[ADR-0003](architecture/decisions/ADR-0003-enki-1-0-ptah-manuscript-shared-draft.md).
Source: `src/domains/agentverse/catalog/` (`models.py`, `service.py`, `capability.py`).
Tests: `src/domains/agentverse/catalog/tests/`.

## 1. Catalog service (CATALOG-0.1)

`ResourceCatalog` answers *which resources of a kind may this requester see?* and *what
exactly does this reference resolve to?* It projects existing authorities; it is not a
second registry:

| Kind | Authoritative source | Read policy reused |
| --- | --- | --- |
| `agent_type` | `AgentRegistry` (`agent`) | `RegistryService.list_for_client` / `get_info_for_client` → `UTUAuthority.can_access_resource` + RG-02F nested checks and safe DTOs |
| `role` | `RoleRegistry` | same |
| `soul` | `PersonalityProfileRegistry` (`personality`) | same |
| `tool` | `ToolRegistry` | same |
| `rulepack` | `RulePackRegistry` | same |
| `workflow` | `coll_workflows` via `WorkflowService` | `list_workflows` / `get_workflow` client read policy |
| `manuscript` | `ManuscriptRegistry` (exact `id@version`, pinned fingerprints) | Manuscript capability's reviewed `gateway_visibility` (tenant, human, source Agent) |

It stores nothing, writes nothing, constructs no Agent or Tool, calls no LLM and holds no
Store, Genesis, Khepri, CPBX, permit or draft handle. Presentation comes only from
already-projected client data (`manifest_projection` DTOs, `ManuscriptDefinition.outline()`).
Raw registry metadata is used only to compute compatibility codes, never serialized.

**List/resolve parity (verified for 0.1A).** Both operations use the same visibility
predicates (`can_access_resource` treats `DISCOVER` and `READ` identically today), the same
Workflow and Manuscript policies and the same entry construction. A listed entry equals
the entry resolved for its exact `key@version` (`test_list_and_resolve_agree_for_every_visible_resource`).
If UTU ever distinguishes `DISCOVER` from `READ`, this parity must be re-reviewed before
the shared signal keeps both operations.

### 1.1 Five separate decisions

| Decision | How it appears | Notes |
| --- | --- | --- |
| **Existence** | `decisions.existence = "exists"` | Observable only together with visibility |
| **Requester visibility** | `decisions.requester_visibility = "visible"`; otherwise `status: "unavailable"` | Unknown, hidden, internal, foreign-tenant, others' private and malformed references are the same result |
| **Candidate compatibility** | `decisions.candidate_compatibility` | Advisory (§1.2) |
| **Candidate attachment** | `decisions.candidate_attachment.status` | `attached` · `not_attached` · `not_evaluated`; Manuscripts are never attached to Agent definitions |
| **Execution authorization** | `decisions.execution_authorization = "not_evaluated"` | The only expressible value. UTU permits + CPBX grants remain the sole authority |

### 1.2 Compatibility integrity (CATALOG-0.1A)

`CompatibilityDecision{advisory: true, status, issues, unevaluated}`. A candidate is an
Agent *being composed* (draft references), never a persisted Agent. **G3.3
`data.validation` remains authoritative** for creation eligibility.

| Status | Meaning |
| --- | --- |
| `compatible` | Unconditional: every applicable check ran on a candidate whose references all resolved |
| `conditional` | No issue found, but a check could not run because the candidate lacks a part (`unevaluated`: `agent_type` for Roles, `role` for Agent Types) |
| `requires_changes` | Only non-blocking issues |
| `incompatible` | At least one blocking issue |
| `not_evaluated` | No candidate supplied |

Codes (reusing `data.validation` semantics without changing the validator):

| Code | Blocking | Subject |
| --- | --- | --- |
| `candidate.reference_unavailable` | no | the **candidate field** (`type`, `role_name`, `personality_key`, `tools`, `workflows`, `rule_pack_ids`), never a resource name |
| `candidate.reference_duplicate` | no | candidate field |
| `composition.role_type_mismatch` | yes | — |
| `composition.required_tool_unavailable` | yes | — (an unreadable required Tool is never named) |
| `composition.required_tool_missing` | no | a **readable** Tool key |
| `composition.workflow_tool_unavailable` | yes | — (defense in depth; the Workflow read policy already hides such Workflows) |
| `composition.rulepacks_insufficient` | no | — |

Hidden and unknown candidate references produce identical findings. Identical findings
collapse, so neither counts nor repetition reveal how many hidden resources are involved.

### 1.3 Contracts (`caos.catalog.*`, version 1.0.0)

Strict, frozen, `extra="forbid"`. Requests carry no identity, tenant, grant, permit,
provider or signal; the requester is the trusted in-process `CatalogRequester{user,
source_agent_id?}`.

| Contract | Shape |
| --- | --- |
| `caos.catalog.list` | `kind`, optional `candidate`, `compatible_only` (drops `incompatible`), `after` cursor, `limit` 1–100 |
| `caos.catalog.list_result` | `kind`, `entries` (sorted by key), `next_after` |
| `caos.catalog.resolve` | `kind`, `value`, optional exact `version`, optional `candidate` |
| `caos.catalog.resolution` | `status` `resolved` · `unavailable` · `version_unavailable` (only for a visible resource), `entry` |
| `caos.catalog.entry` | `ref`, `identity`, `details` (discriminated by kind), `decisions`, `descriptor_digest` |
| `caos.catalog.resource_ref` | `kind`, `key`, `version`, `fingerprint` (Manuscripts only); `address = key@version` |

Exact versions: AOSA registries hold one version per key and a requested version must equal
it; Manuscripts resolve only by exact `id@version` (no "latest"). `descriptor_digest` is
SHA-256 over `ref + identity + details`, independent of candidate decisions.

## 2. Enki Catalog capability — `catalog.project@1.0.0` (CATALOG-0.1A)

Approved by the architect: Enki only, provider `system.catalog`, exact version, single-signal
attenuated UTU permit, live human session, no bootstrap permits, no trusted line,
requester-visible projections only, no mutation authority.

| Concern | Implementation (`catalog/capability.py`) |
| --- | --- |
| Binding | `SynapticRegistry` exact `catalog.project@1.0.0 → system.catalog`; registered at startup only when the reviewed policy grants it; `validate_catalog_binding` fails startup if granted but unbound; any trusted line targeting it fails startup (`catalog_policy_state`) |
| Grant | `config/genesis.local.json`: Enki only. `genesis_local._ENKI_CATALOG_GRANTS` pins the accepted set; no other Agent or System Citizen may hold it. Local policy revision `f79a003f…` → **`1a960bd4…`** |
| Scope | `SCOPE = CapabilityScope(catalog.project, BINDING, {BINDING})`; trusted caller `project_catalog()` attenuates before binding and revokes/drains after |
| Provider checks | Exact signal/target/tenant/source, scope, binding chain, entry operation, provider version, `origin == "session"`; `check_permit`; current principal's policy versions equal the permit's; grant still present. Rechecked (`check_permit`) after the read before releasing a result |
| Requester | `UTUAuthority.resource_requester(permit)` (trusted session adapter, tenant/user must match the permit); source Agent = `permit.key.agent_id`. Nothing from the payload |
| Command | `caos.catalog.project` v1: `{contract, schema_version: 1, operation: "list"\|"resolve", request}` with the §1.3 request contracts; strict JSON-mode validation; ≤ 16 KiB; foreign fields, unknown operations and non-catalog kinds → `{ok: false, code: "catalog.invalid_request"}` |
| Result | `caos.catalog.project_result{ok, operation, result}` — exactly the human's own `ResourceCatalog` answer for that source Agent |
| Effects | None: read-only; no Seshat record (not consequential), no Store/Genesis/Khepri/draft/Manuscript handle |
| Generic route | `POST /agents/{id}/signals/catalog.project` carries an unscoped permit and is refused (403) |
| Nataraja | `DECLARED_PROVIDERS` declares `system.catalog@1.0.0` |

Enki's turn plan does not call it yet (ENKI-1.0-B).

## 3. Manuscript catalog port

`manuscripts/catalog.py`: `GovernedManuscriptCatalog` implements the unchanged
`ManuscriptCatalog.resolve(kind, value, requester)` through `ResourceCatalog.resolve_ref`
(kinds `agent`→`agent_type`, `personality`→`soul`; human requester only).
`RegistryManuscriptCatalog` remains a constructor-compatible subclass. Startup builds one
`app.state.resource_catalog` shared by the port and the capability. Definitions, published
fingerprints, session pins and the six `manuscript.session.*` signals are unchanged.

## 4. Ptah-ready presentation metadata (declarative only)

No URL, endpoint, signal, provider, handler, raw config, class path, tenant ID, creator or
session data. Verified against the real `manuscript` Tool with injected sentinels.

| Field | Content |
| --- | --- |
| `identity.title`, `summary` | Bounded 120 / 500 |
| `identity.icon` | Declared icon token or emoji (≤ 64), presentation only |
| `identity.media` | Reserved `asset:<id>@sha256:<digest>`; **always `null`** (no asset service, avatars or sigils) |
| `identity.fallback_icon` | Deterministic `{style: monogram, glyph: <kind>, text: ≤2 initials, hue}`; hue = SHA-256(`kind:key`) mod 360 |
| `identity.category`, `tags`, `scope` | `scope` is `global` or `tenant` (never the tenant ID) |
| `details` (tool) | `tool_class`, `min_rulepacks`, `configuration{configurable, fields, config_schema}` from RG-02F `ui.admin.fields` / `agent_config_schema` |
| `details` (role) | `agent_group`, `agent_subclass`, readable `required_tools` / `optional_tools` |
| `details` (other kinds) | agent type: group/subclass · soul: none · rulepack: `domain`, `rule_count` · workflow: `input_count` (no steps) · manuscript: `artifact_contract`, stage outline |

**Planned Ptah consumers (not implemented):**

| Ptah consumer (proposed) | Catalog data it would bind |
| --- | --- |
| `catalog.tool_store@1.0.0` | `list(kind="tool", candidate=<draft refs>)`; show compatibility, attachment and "execution not evaluated" separately; placeholder binding `catalog.tools.eligible_for_candidate@1.0.0` ≈ `compatible_only=True` |
| `tool.card@1.0.0` | `identity`, `ref.version`, `details.configuration.configurable`, compatibility status |
| Tool configuration panel | `details.configuration`; saving uses DRAFT-0.2B CAS and server validation, never the catalog |
| Role / Soul selectors | `list(kind="role"\|"soul", candidate=…)` |
| Manuscript selector | `list(kind="manuscript")` with a source Agent; `ref.address` feeds `manuscript` Tool `default_manuscript` |
| Reasoning task selector | `reasoning` Tool `details.configuration.fields[tasks].options` (published task key, title, description); feeds `reasoning` Tool `tasks` |

`ToolConfigField.options` (REASONING-0.1C, additive, ≤ 32, string values only) is copied
from the already-sanitized RG-02F admin field; it is descriptive and never expresses
availability or authority.

A human-facing V2 projection route belongs to PTAH-0.1; none exists.

## 5. Verification (2026-10-30)

Isolated container from the existing `caos-api-api` image on an `--internal` network with
disposable MongoDB and Redis; `src/` and `config/` mounted read-only; `AUTHZ_ENABLED=false`
and a test-only signing key (UTU/CPBX active). The live API remained paused (operator
approval) and no live database or Redis was accessed.

- Catalog tests: **73 passed** — `test_resource_catalog.py` (service, compatibility
  integrity, list/resolve parity), `test_catalog_capability.py` (provider guards: bootstrap,
  foreign scope, unscoped, binding chain, version, entry operation, tenant/source mismatch,
  revocation, stale policy, withdrawn grant, revocation during the read, strict commands,
  requester from the session adapter, uniform hidden/unknown, no mutation),
  `test_catalog_capability_integration.py` (real local policy: Enki-only holder, exact
  binding, no trusted lines, results equal the human's catalog, Manuscript fingerprint,
  cross-scope denial both ways, Genesis/JOSHUA cannot narrow, generic route 403, no
  Agent/approval/creation/audit/draft/session effect, logout denial).
- Focused regression (catalog, manuscripts, Manuscript Tool, `tests/tools`, `tests/agents`,
  `tests/services`, `tests/registries`, lifecycle, Nataraja, NCSC): **1552 passed**.
- Full backend: **2745 passed, 9 skipped, 5 failed** — the 5 inherited
  `test_root_signing_key_wiring.py` fixture failures.
- Static: Ruff check/format pass on all changed files; mypy passes on `catalog/*.py`,
  `manuscripts/catalog.py`, `nataraja/plan.py`. **Inherited:** 8 mypy `attr-defined`
  findings in `dependencies/genesis_local.py` (dependency-injector `Provider` typing),
  identical at `HEAD`.

## 6. Known limitations

- Registry kinds have one version per key; only Manuscripts pin content fingerprints.
- Visibility is evaluated per item; cost is linear in registry/Workflow size (no cache).
- Compatibility covers type/Role groups, Role-required Tools, RulePack minimums and
  Workflow Tool registration; GUI, V2 conversation and Tool configuration checks remain in
  `data.validation`.
- Manuscripts are visible only with a source Agent that has a reviewed gateway binding for
  the human.
- The inherited `data.validation` disclosure found here (a Role's unreadable required Tool
  named in a finding) is corrected by VALIDATION-SEC-0.1 — see
  [Security Findings](SECURITY_FINDINGS.md).
- Resuming the live API installs policy revision `1a960bd4…` (adds Enki's `catalog.project`)
  through normal bootstrap; that is an operator decision.
