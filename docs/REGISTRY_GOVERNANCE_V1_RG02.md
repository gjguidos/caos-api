**RG-02D update:** Missing ownership no longer implies global access. See [resource ownership governance, verification and migration plan](REGISTRY_GOVERNANCE_V1_RG02D.md). The report below is a historical snapshot.

# Registry Governance V1 — RG-02

Date: 2026-10-26. Checkout: `/Users/guidoguidos/Development/caos/caos-api`.
Branch: `feature/ncsc-pbx`. HEAD: `1207b1f`.

RG-02 client catalog/read protection is implemented in the current checkout. The pre-existing, uncommitted RG-00/RG-01 implementation is preserved. Nothing was committed or pushed. RG-03, Enki, new tools and client redesign were not implemented. Registry Governance V1 is not complete until RG-03 is addressed.

## Policy and boundaries

- `UTUAuthority.can_access_resource()` owns DISCOVER/READ decisions. It requires an authenticated `User` identity, applies the existing exposure classification, and denies internal/system/builtin/hidden resources even to administrators. Private metadata uses the existing owner/group/admin helpers, after tenant/exposure checks. These read decisions grant neither tool attachment nor invocation authority.
- HTTP JWT middleware and V1 socket authentication copy `tid` only from verified claims into `User.tenant_id`. Query strings, tenant headers, request principal objects and payload tenant fields establish no authority. A resource with a tenant must match that verified tenant; missing, empty, or foreign context denies. An explicit `tenant_scoped` resource without a valid tenant also denies.
- Legacy resources without a tenant remain global. There is no inferred default tenant or automatic data migration. Newly created agents and workflows persist the authenticated user's tenant; payload tenant/exposure cannot replace it.
- Registry HTTP list/detail endpoints use `RegistryService.list_for_client/get_info_for_client`. The client allowlist is agent, personality, tool, role, rulepack and workflow. Synaptic and tool_runtime_dependency kinds cannot be queried, even with a known key. Missing and denied detail reads retain the existing error-object shape; forbidden list kinds return 404.
- Generic `Registry` remains independent of UTU. Raw registry and service lookup/list methods remain trusted Python runtime/bootstrap interfaces. They must not be used by a future client route in place of the guarded facade.
- Public catalog serialization remains unchanged, including the existing flat tool catalog. Caller-supplied include-system/include-hidden overrides are absent from the client facade.

## Nested, soul and workflow reads

The service checks recognized tool, role, personality and rule-pack references, required/default tools, inline nested metadata, and workflow command references. Command keys use the existing workflow runtime parser, including whitespace handling. Missing or protected references deny the enclosing detail response and omit the enclosing listing entry. No partially redacted executable workflow is returned. Traversal is bounded; shared runtime metadata is not mutated.

Schema dictionaries (`parameters_schema`, `config_schema`, `input_jsonschema`, `output_jsonschema`) describe fields rather than expanded resource records. A schema property named tenant_id or exposure is not treated as resource authority. New reference formats require an explicit mapping; this is not a free-text secret detector.

Personality/soul access checks registration metadata before class construction, then checks the actual minimal soul expansion before returning it. Trusted `extract_soul()` remains available to internal code.

Persisted workflow list/detail reads check the raw database document before Pydantic can discard policy fields, including nested references. No-principal calls to these client methods deny/omit. Trusted bulk `get_workflows`, required-tool resolution, and `resolve_definition` retain internal functionality. Workflow PATCH/no-op PATCH and deletion also check the read boundary, preventing a known protected key from bypassing GET denial through a write path. Related workflow/tool upload constraint reads now carry authenticated identity and apply the same catalog policy.

## Persisted internal agents

`Agent.exposure` is independent of existing public/private `visibility`; historical agents default to public exposure. `Agent` and `WorkflowDocument` carry persisted exposure and tenant_id fields. Those fields are additive on serialized entity responses; existing endpoints, envelopes and catalog shapes remain intact. The current Next.js registry/workflow consumers were inspected and tolerate these additive fields; no client changes or browser validation were performed.

Agent read and write checks reject internal/foreign-tenant definitions before owner/group/admin rules. The Mongo discovery filter excludes internal exposure, and the service rechecks every returned entity with DISCOVER policy. Direct reads also verify the returned persisted ID matches the requested ID. Agent PATCH rejects changes to server-owned ID, creator, user_id, tenant, exposure, runtime and provenance.

V1 chat already rechecks `get_agent` before cache reuse or runtime construction, so it inherits internal-agent denial. V2 UTU conversation authorization reads every conversation participant, even for detail/history calls without an explicit target subset, and denies internal/foreign/mismatched definitions before dispatch. Trusted V2 principal resolution remains available for internal agents subject to its existing session, approval, tenant, grant and revocation checks.

Internal exposure is provisioned by trusted persisted definitions; no client toggle or new internal-agent provisioning API was introduced. Existing stored agents are not automatically reclassified.

## RG-02 changed files

Paths are relative to the checkout. This list includes files shared with the prior RG-00/RG-01 work.

| File | Change |
| --- | --- |
| `src/base/entities/user.py` | Verified tenant context |
| `src/base/middlewares/jwt_middleware.py` | HTTP verified tid propagation |
| `src/base/decorators/middleware/ws_session_based.py` | V1 socket verified tid propagation |
| `src/domains/agentverse/entities/agent.py` | Persisted exposure/tenant distinction |
| `src/domains/agentverse/entities/agent_update_request.py` | Reject forged server-owned policy/identity updates |
| `src/domains/agentverse/entities/workflow.py` | Persisted workflow exposure/tenant fields |
| `src/domains/agentverse/security/utu.py` | DISCOVER/READ decision and V2 internal conversation gate |
| `src/domains/agentverse/security/agent_access.py` | Internal/tenant guards and listing filter; preserves RG-00 creation guard |
| `src/domains/agentverse/services/agent_service.py` | Trusted tenant persistence, ID binding and defensive listing checks |
| `src/domains/agentverse/services/registry_service.py` | Guarded client facade, kind allowlist and nested checks |
| `src/domains/agentverse/services/personality_service.py` | Guarded soul extraction/expansion |
| `src/domains/agentverse/services/workflow_service.py` | Governed persisted reads and write-response protection |
| `src/domains/agentverse/interfaces/api/v1/interface.py` | Pass authenticated identity to registry/soul reads |
| `src/domains/agentverse/interfaces/api/v1/file_routes.py` | Govern tool/workflow constraint reads before body processing |
| `src/domains/agentverse/interfaces/api/v1/workflow_routes.py` | Correct read-policy documentation |
| `src/domains/agentverse/tests/services/test_catalog_governance.py` | New security regressions |
| `src/domains/agentverse/tests/communication/test_cognitive_response_v2.py` | Preserve per-ID fixture lookup for non-primary conversation participants |
| `src/domains/agentverse/tests/registries/test_tool_registration_governance.py` | Make prior catalog tag-equivalence assertion independent of set iteration order; no RG-01 production change |
| `docs/REGISTRY_GOVERNANCE_V1_PROGRESS.md` | Link this follow-on report |
| `docs/REGISTRY_GOVERNANCE_V1_RG02.md` | This report |

Unchanged from the incoming RG-00/RG-01 work: `agents/factory.py`, `registries/base.py`, `registries/tool_registry.py`, `tools/spec.py`, `entities/resource_exposure.py`, and `tests/services/test_creation_governance.py` under `src/domains/agentverse/`. The RG-01 test adjustment above changes comparison order only.

## Verification

All tests used the existing API Docker container and `python -m pytest -q -p no:cacheprovider`. No paid model calls. Redis tests used database 15 through the existing configured connection, randomized namespaces and targeted cleanup; no flush or credential output.

1. **367 passed**: new catalog security tests, all registry tests, RG-00 creation, workflow service, authenticated V1 socket middleware, all V2 communication/history/cognitive response tests and the full agent lifecycle/session/UTU/revocation suite. All Redis integration tests ran, with no skips.
2. **347 passed**: runtime compatibility, workflow attachment validation/key normalization, workflow validation/seeding/spec inputs, ToolSpec scope and ToolManager authorization, all agent tests, file service/validation, and all workflow tool tests.

The final suites total **714 passed**, with 11 existing Pydantic deprecation warnings per invocation.

The new security suite covers omissions and direct keys for every client registry kind, forbidden registry kinds, unauthenticated/request-shaped identities, public/global reads, private owner/group policy, forged principal/tenant payloads, cross-tenant and absent-tenant reads, nested references/inline metadata, soul preconstruction and expansion denial, workflow reads and trusted bulk resolution, internal V1 cached chat denial, persisted-ID mismatch, internal V2 trusted principal resolution versus conversation denial, protected write responses, upload metadata and authenticated creation tenant persistence.

Intermediate findings were fixed: a Python annotation collision with the existing RegistryService.list method; a V2 test fixture that returned the primary definition for every ID; and an existing RG-01 test that compared catalog tag set projections by list order. Final results above are after those fixes.

- New security test file and UTU: full Ruff checks pass.
- Six focused policy/service/test files pass Ruff formatting checks.
- Ruff comparison of all tracked changed Python files against HEAD: 81 baseline diagnostic instances, 80 current, **no added diagnostic signatures/counts**. Existing executable-file, FastAPI dependency-default and other unrelated warnings were not broadly fixed.
- Limited mypy: registry and personality services pass with `--explicit-package-bases --follow-imports=silent`. This is not a whole-project type-check claim.
- `git diff --check` passes.

## Security caveats and remaining work

- **RG-03 remains open:** known-key tool attachment, internal/system invocation consistency, merge/override restrictions and production synaptic freeze are not completed by read policy. Catalog omission is not an invocation revocation mechanism.
- Trusted in-process registry, factory, bulk workflow and principal-resolution APIs intentionally retain access. They are not an isolation boundary for hostile Python code and must not be exposed directly to clients.
- Existing unscoped records remain global; tenant isolation cannot be retroactively inferred. A trusted migration must classify historical tenant/internal resources correctly.
- A parent manifest with a protected dependency is omitted/denied in full. This can make an otherwise public role or workflow unavailable in the client catalog; it deliberately avoids leaking the protected dependency or returning an incomplete definition.
- Typed references and resource metadata are protected; arbitrary prose, schema documentation and future opaque reference formats require proper author classification and explicit adapters.
- No end-to-end Next.js/browser run, whole-repository pytest run, or whole-project mypy run is claimed. The earlier RG-00/RG-01 report's unrelated WebSocket teardown failures were not repaired here.
