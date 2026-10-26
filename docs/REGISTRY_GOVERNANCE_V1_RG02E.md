# Registry Governance V1 — RG-02E

Date: 2026-10-26 (Europe/Stockholm). Checkout: `/Users/guidoguidos/Development/caos/caos-api`. Branch `feature/ncsc-pbx`, HEAD `1207b1f` unchanged. No commit or push. Existing RG-00/RG-01/RG-02/RG-02D and unrelated work was snapshotted before editing and preserved. RG-03, invocation/attachment controls, Enki, channels and UI redesign are untouched.

RG-02E code classification, read-only development inventory and authenticated live Adam conversation verification are complete. The user explicitly approved the bounded local session probe after the initial automatic approval review rejection. Persisted ownership review remains deliberately pending; no ownership migration was authorized or performed.

## Classification decisions and evidence

Ownership is authored at individual trusted declaration sites, never as a registry default or inferred from tags, names, creator IDs, absent tenants or runtime approvals. Exposure and ownership remain independent. The reconstructed production registrations contain **39 entries: 32 global and 7 system, none unclassified**. Every client-visible result was compared with the running API. This does not prove the absence of additional non-visible registrations in another worker or deployment.

| Kind | Explicit decisions | Evidence in implementation |
| --- | --- | --- |
| Agent types (3) | `chat`, `form`, `orchestration`: global | Configurable reusable `AgentSpec` templates in `agents/specs/*_agent.py`, bound to implementations; instances retain independent persisted ownership. |
| Roles (4) | `general`, `crawler`, `writer_assistant`, `compliance_auditor`: global | Reusable role policy, handlers and frontend manifests in `agents/roles/*/spec.py`; `RoleRegistry.register_role_spec()` serializes the spec. Internal dependencies still deny whole-parent reads. |
| Personalities (10) | All ten static declarations: global | Static `AgentSoulProtocol` blueprint classes in `personalities/*.py`; predefined archetype purpose documented by `PersonalityProfileRegistry`. No tenant-specific records are assigned. Exact keys are in the JSON inventory. |
| Rulepacks (2) | `gdpr_compliance`, `green_claims_compliance`: global | Static reusable content-review `RulePackSpec` factories in `agents/roles/rules/{gdpr_pack,green_claim_pack}.py`. |
| Tools (10 global, 6 system) | Global: `datetime`, `math`, `draw`, `rag`, `writer`, `ideation`, `green_claim`, `auditor`, `web_crawler`, `workflow`. System: `analyze`, `refine`, `evaluate`, `cta`, `chat`, `chat_v2`. | Global tools have reusable capability/configuration declarations. System tools implement orchestrator phases, CTA synthesis, or conversation context/invoker infrastructure. Both classification decisions come from implementations and call paths, independently of existing tags. |
| Workflows (4) | `crawl_operation_v1`, `mini_workflow_v1`, `nested_mini_workflow_v1`: global; `debug_two_mystic_images_v1`: system | Three explicit `BUILTIN_WORKFLOWS` factories already define new shared seeds in RG-02D. The fourth is a diagnostic of two Mystic image calls and is excluded from that seed catalog. Registry declarations now agree with those semantics. |

`WhispererTool`'s trusted late-registration metadata also explicitly declares global. Importing its module does not register it; it was absent from this environment's catalog. MCP loading is disabled locally. No blanket ownership was added to `MCPToolSource`, registry base classes, arbitrary external ToolSpecs or legacy records.

The existing Genesis seed and builtin persisted-workflow seed definitions already declare correct ownership from RG-02D, so RG-02E changes neither seed implementation nor stored records. Historical Adam's seed marker and fixed ID are observations for review, not migration authority.

## Implementation and nested references

- `AgentSpec` and `RoleSpec` accept nullable explicit ownership; their defaults remain unclassified. `ToolRef` gains an optional canonical `key`; reviewed chat/orchestration declarations author `datetime`/`workflow` explicitly while preserving existing display names/provider keys. No display-name ownership inference or lookup fallback was introduced.
- `security/resource_references.py` centralizes supported reference extraction for read authorization and inventory. Besides the RG-02D lists and single references, it covers role required/optional tool map **keys**, system-tool map **values**, required-workflow map **values**, typed tool/workflow descriptors, `uses_workflows`, `workflow_key`, auditor rulepack hints, and literal `@workflow --workflow_key` targets. Missing, ambiguous, malformed or unresolved targets deny access. Schema defaults remain excluded; opaque prose is not parsed into authority.
- `RegistryService` still uses UTU and the existing whole-parent denial, independent reference classification, bounded traversal, ownership-context inheritance and raw trusted registry interfaces. No providers are instantiated for catalog authorization.
- V2 conversation checks now pass the **complete persisted policy** to UTU instead of projecting away `tenant_scoped`, visibility and legacy exposure restrictions. Regressions cover conflicting global scope, system tags and another owner's private record. Runtime approval remains separate from client read ownership.
- The ownership inventory CLI adds `--review-manifest`. `ownership_review.py` emits canonical complete-record hashes, exact missing/null policy and identity/revision preconditions, dependencies, observation fields, missing/duplicate identifier blockers and empty proposal/reviewer/evidence fields. `apply_authorized` is always false. There is no migration writer or database access in either inventory module.

Read checks do not grant attachment/invocation capabilities. Generic runtime dependencies and synaptic bindings remain internal interfaces. `persona_profile="GenericEVAProfile"` is an unused descriptive AgentSpec field (the only uses are the model and chat/form declarations), not a registered personality address; actual personality selection uses `personality_key`.

## Development inventory and review manifest

Per-record administrative inventory, immutable identifiers, hashes, tenant details, session identifiers and protected snapshots are deliberately excluded from version control. Retain authorized exports privately, not as client endpoints or source documentation. Temporary snapshots are not a durable backup.

Development inspection found unclassified historical agents and no persisted workflows. Historical seed markers and runtime approvals were treated as observations, not authority to assign ownership. Proposals, reviewer and ticket remain unset and application is unauthorized. No unknown row was updated or deleted; persisted snapshots were unchanged by verification. Existing conversation documents were inspected without reading history bodies or reusing sessions.

Replay an authorized export with the enhanced inventory command:

```bash
docker compose exec -T api python -m src.domains.agentverse.security.ownership_inventory /path/to/authorized/agents.json --source coll_agents --review-manifest
docker compose exec -T api python -m src.domains.agentverse.security.ownership_inventory /path/to/authorized/tools.json --source registry:tool --review-manifest
```

A later migration still requires separate per-record approval and a fresh snapshot/CAS precondition check under RG-02D's migration plan. No tenant, global default or bulk approval was invented here.

## Actual client-facing development verification

Read-only HTTP calls used short-lived locally signed development JWTs through the running API's normal JWT middleware. Tokens/secret values were never printed or stored in reports. All six live catalogs matched the independently reconstructed production registry service catalogs after importing the actual implementation modules; import errors: zero. Existing response envelopes (`data`, `success`, `statusCode`, versions and message) remain unchanged.

| Surface | Observed live result |
| --- | --- |
| Agent-type catalog | `form`, `orchestration`; `chat` omitted/known-key denied because its explicit `datetime` dependency retains legacy internal exposure. |
| Personality catalog and souls | All 10 templates and all 10 soul projections succeed. `empty_eva` soul also succeeds with another tenant JWT, consistent with explicit global sharing. |
| Rulepack catalog/details | Both templates succeed. Auditor references resolve to the two independently classified rulepacks. |
| Tool catalog | `workflow`, `rag`, `draw`, `auditor`, `writer`, `green_claim`, `ideation`. System known keys deny. `datetime` and `math` remain denied by existing builtin/system exposure rules. |
| Role catalog/details | Empty; all four known-key reads deny. General/writer/compliance retain internal phase dependencies; crawler has unavailable/internal references. |
| Workflow registry catalog/details | Mini and nested mini succeed; nested mini independently resolves its child. Crawl and diagnostic known keys deny. |
| Persisted workflows | List returns `[]`; known persisted mini key returns 404 because no row exists. A readable registry template is not a persisted workflow record. |
| Persisted agents | List returns `[]`; both known IDs return 403 for local-owner/admin and other-tenant JWTs. This proves unknown-record denial; positive matching-tenant behavior and tenant isolation are covered by tests because no classified persisted agent exists here. |
| Internal/unknown registries and keys | Synaptic/runtime-dependency catalogs return 404. Unknown and protected known-key lookups preserve the existing `{error: ...}` body within HTTP 200 envelopes. |

The crawler tool remains denied under inherited RG-02D nested exposure checks: `ui.components[9]` and `[10]` declare `hidden`, so the whole tool manifest fails closed. Its role additionally references missing `normalizer`, `store` and `artifact_builder`; crawl workflow requires `artifact_builder`. The diagnostic workflow references missing `mystic_image`. These are unresolved capabilities, not records assigned guessed ownership. Existing restrictions were not cleared merely to make catalogs larger. Changing public projections of internal composition/UI metadata needs separate design/review.

**Authenticated live Adam conversation verification completed:** on 2026-10-26 at 22:12:54 Europe/Stockholm, following explicit user approval, one fresh fixed-local-fixture session was provisioned through the normal API and logged out. All **28 HTTP checks passed**:

- Session provisioning returned 201; authenticated session status returned 200 both before and after the denied resource checks. The resource denials therefore occurred inside a working authenticated session.
- Adam conversation entry returned 403. All 11 existing conversation detail reads and all 11 history reads returned 403, as required while historical Adam remains unclassified.
- Logout returned 204; a subsequent session status returned 403. The durable session audit record was independently confirmed `revoked`.
- Mongo agent/workflow hashes, existing Redis conversation/journal/history state hashes and the complete conversation-document key set matched before/after. No conversation was created; no resource ownership, content or history changed. No model call or agent activation occurred.

The initial automatic approval rejection remains an audit fact: it identified session provisioning and the conversation POST as potential persistent state changes beyond read-only verification. That action did not execute until the user expressly approved this exact probe. One new terminal session audit record and its normal session revocation state remain; no existing session was reused or modified. Detailed verification artifacts remain private and are excluded from version control. Bearer tokens and secret values are omitted.

## Regression tests and quality checks

- Final focused ownership/catalog/classification suite: **174 passed**, no skips, 11 existing Pydantic warnings. New RG-02E file has 34 cases covering explicit declarations without defaults, map directions, workflow targets, nested unknown/system/foreign denial, rulepack hints, review hash/precondition/no-assignment behavior, and full V2 persisted-policy enforcement.
- Final broader compatibility suite: **847 passed, 1 failed, no skips**, 12 existing deprecation warnings. Includes all service, registry, workflow, agent and communication test directories; lifecycle/session/UTU/revocation tests; tool and WorkflowTool tests; V1 authenticated socket middleware. Redis integrations used configured local Redis DB 15, randomized fixture prefixes and targeted cleanup; no shared DB flush and no paid/external LLM calls.
- The failure is `tools/tests/test_chat_tool_v2.py::test_standard_tool_execution_envelope`: it calls `_run(text="Hello")` without required `socket_emitter`. Executing the snapshotted start-of-task ChatToolV2 source reproduces the same TypeError. Its only RG-02E edit is registration ownership metadata. The unrelated failure was preserved.
- Limited mypy: success for ownership inventory, review manifest, reference extractor and registry service with `--explicit-package-bases --follow-imports=silent`. No whole-project mypy claim.
- New helpers, enhanced inventory and new tests pass targeted Ruff check; these plus RegistryService pass Ruff format checks. Read-only comparison across all 48 affected Python files using the exact start-of-task source shows **114 baseline diagnostics, 114 current, zero added signatures/counts**. Existing executable-mode and style diagnostics were retained.
- `git diff --check` passes. All original snapshotted files outside the exact RG-02E list below remain byte-for-byte unchanged; HEAD and branch are unchanged. No browser UI end-to-end claim.

One broad auto-fix attempt was rejected by automatic approval review to protect existing uncommitted work. A read-only Ruff diff was then reviewed; a narrowly scoped retry limited to the new import and whitespace formatting succeeded. No repository-wide fixes or unsafe refactors were applied.

## Exact RG-02E file changes

This list is relative to the checkout and compares to the start-of-task snapshot, including pre-existing untracked files. All declaration-only files gain explicit ownership; the two AgentSpecs also gain canonical ToolRef keys. The implementation changes are explained above. Prior reports remain intact except the additive progress link.

- `docs/REGISTRY_GOVERNANCE_V1_PROGRESS.md`
- `docs/REGISTRY_GOVERNANCE_V1_RG02E.md`
- Private administrative inventory (excluded from this commit)
- `src/domains/agentverse/agents/roles/builtin/spec.py`
- `src/domains/agentverse/agents/roles/compliance_auditor/spec.py`
- `src/domains/agentverse/agents/roles/crawler/spec.py`
- `src/domains/agentverse/agents/roles/role_spec.py`
- `src/domains/agentverse/agents/roles/rules/gdpr_pack.py`
- `src/domains/agentverse/agents/roles/rules/green_claim_pack.py`
- `src/domains/agentverse/agents/roles/writer_assistant/spec.py`
- `src/domains/agentverse/agents/specs/agent_spec.py`
- `src/domains/agentverse/agents/specs/chat_agent.py`
- `src/domains/agentverse/agents/specs/form_agent.py`
- `src/domains/agentverse/agents/specs/orchestration_agent.py`
- `src/domains/agentverse/personalities/brand_guardian.py`
- `src/domains/agentverse/personalities/creative_director.py`
- `src/domains/agentverse/personalities/empty_eva.py`
- `src/domains/agentverse/personalities/escher.py`
- `src/domains/agentverse/personalities/ideator.py`
- `src/domains/agentverse/personalities/notorious_big.py`
- `src/domains/agentverse/personalities/phife_dawg.py`
- `src/domains/agentverse/personalities/shakespeare.py`
- `src/domains/agentverse/personalities/social_chameleon.py`
- `src/domains/agentverse/personalities/yoda.py`
- `src/domains/agentverse/security/ownership_inventory.py`
- `src/domains/agentverse/security/ownership_review.py`
- `src/domains/agentverse/security/resource_references.py`
- `src/domains/agentverse/security/utu.py`
- `src/domains/agentverse/services/registry_service.py`
- `src/domains/agentverse/tests/services/test_ownership_classification.py`
- `src/domains/agentverse/tools/analyze_tool.py`
- `src/domains/agentverse/tools/auditor_tool/specs.py`
- `src/domains/agentverse/tools/chat_tool.py`
- `src/domains/agentverse/tools/chat_tool_v2/__init__.py`
- `src/domains/agentverse/tools/cta_tool.py`
- `src/domains/agentverse/tools/date_time_tool.py`
- `src/domains/agentverse/tools/draw_tool.py`
- `src/domains/agentverse/tools/evaluate_tool.py`
- `src/domains/agentverse/tools/green_claim_tool/spec.py`
- `src/domains/agentverse/tools/ideation_tool/spec.py`
- `src/domains/agentverse/tools/math_tool.py`
- `src/domains/agentverse/tools/rag_tool.py`
- `src/domains/agentverse/tools/refine_tool.py`
- `src/domains/agentverse/tools/web_crawler_tool/spec.py`
- `src/domains/agentverse/tools/whisperer_tool.py`
- `src/domains/agentverse/tools/workflow_tool/spec.py`
- `src/domains/agentverse/tools/writer_tool/spec.py`
- `src/domains/agentverse/workflows/crawl_operation_v1/crawl_operation_v1.py`
- `src/domains/agentverse/workflows/debug/debug_image_tools.py`
- `src/domains/agentverse/workflows/mini_workflow/mini_workflow.py`
- `src/domains/agentverse/workflows/nested_mini_workflow/nested_mini_workflow_v1.py`
