**RG-02F update:** Safe client projections and final crawler compatibility changes are included. See [projection boundary, verification and remaining work](REGISTRY_GOVERNANCE_V1_RG02F.md). RG-03 remains unfinished; browser E2E verification is pending.

**RG-02E update:** Trusted code ownership is explicitly classified, development data inventoried read-only, and authenticated live Adam conversation verification completed after explicit approval (28 checks passed). Persisted ownership review remains pending. See [classification decisions, exact changes, verification and blockers](REGISTRY_GOVERNANCE_V1_RG02E.md).

**RG-02D update:** Missing ownership no longer implies global access. See [resource ownership governance, verification and migration plan](REGISTRY_GOVERNANCE_V1_RG02D.md). The report below is a historical snapshot.

# Registry Governance V1 — RG-00 / RG-01 implementation

**Follow-on update:** RG-02 is now implemented in this checkout. See [RG-02 implementation, changed files, verification and caveats](REGISTRY_GOVERNANCE_V1_RG02.md). RG-03 remains open. The RG-00/RG-01 report below is the preserved historical snapshot from before RG-02.

Date: 2026-10-26. Checkout: `/Users/guidoguidos/Development/caos/caos-api`.
Branch: `feature/ncsc-pbx`. Starting HEAD: `1207b1f` (clean).

RG-00 and RG-01 are implemented. Registry Governance V1 as a whole is **not complete**: RG-02 and RG-03 remain. No commits, pushes, client edits, endpoint changes, Enki implementation or new tools were made.

## Stage RG-00: shared creation authorization

`AgentService.create_agent()` now requires a server-authenticated `User` with a nonempty identity and either `agentverse-admin` or a nonempty `<group>.admin` role. The existing whitelist membership validation remains in force, including for admins. An ordinary USER, an unidentified admin, a missing user, or a dict posing as a user receives `AGENT_ACCESS_DENIED` (403).

The check precedes duplicate reads, config preparation, factory construction, storage and operation audit calls. Both V1 sockets route through this same boundary. It does not depend on the optional external authorization service. The service assigns `agent_config.user_id` from the authenticated User, including for direct callers. Payload owner, principal, roles, tenant and grants do not establish creation authority. Existing AgentRequest runtime/provenance rejection remains unchanged.

Direct Python callers must supply the server-authenticated User argument; this is the existing trusted service interface, not a new authentication mechanism for arbitrary Python callers. No delegated-creation or tenant grants are introduced.

Real socket-handler tests exercise event registration, DivineOrchestrationService, AgentService, and the existing emitter. They enter the authenticated socket bodies via `__wrapped__`, mocking transport/infrastructure rather than retesting JWT middleware. Both endpoints deny USER creation and accept superadmin/group-admin creation. Rejected calls cause no duplicate read, config preparation, factory call, persistence, audit operation or agent test. Socket logs and one terminal ERROR result are expected. Successful terminal payloads retain their existing shape.

## Stage RG-01: declaration and exposure normalization

A shared `ResourceExposure` contract supports `public` and `internal`. `classify_resource()` combines this with existing system/builtin/hidden heuristics, without rewriting raw metadata. `RegistryItem.exposure` provides the normalized classification as a Python property and does not add serialized response fields.

`ToolSpec.exposure` is optional and separate from invocation `scope`. Explicit public exposure cannot clear legacy restrictions. Explicit internal exposure is absent from `list_public()`, even with legacy include flags. Existing `include_system`, `include_builtin`, and `include_hidden` behavior remains available for legacy classifications. This is catalog classification, not principal authorization or an execution grant.

`ToolSpec.registration_metadata()` forwards scope, exposure, UI, tags, config, outputs, supports, synaptic, tool_class and source. Explicit top-level fields win using existing ToolSpec hydration semantics. Omitted fields preserve metadata rather than replacing it with empty defaults. Existing output/UI hydration behavior is retained. Legacy tags retain their spelling and order; explicitly authored top-level tags retain ToolSpec's existing canonicalization/sorting. Catalog tag matching continues to be case-insensitive. Arbitrary unrelated metadata is retained without forcing it through JSON serialization. Disabled registration, class defaults, output validation and registry replacement behavior remain intact.

Factory expansion carries trusted metadata, UI, tags, outputs, supports, exposure and synaptic declarations into runtime specs, along with the registered description/version. Config merges class/registry default_config, declared registry config, then agent-specific overrides. The invocation scope supplied by the composition path remains authoritative; registry scope remains available in metadata. A config key named synaptic/exposure is still configuration, not a replacement for the trusted top-level declaration. Synaptic declarations register no handlers and create no grants.

The dead tuple-returning `Registry.items()` definition is removed. The effective contract remains `list[RegistryItem]` in registration order; `values()` still returns implementations.

## Changed files

All paths below are relative to the actual checkout above.

| File | Change |
| --- | --- |
| `src/domains/agentverse/security/agent_access.py` | Creation predicate and corrected access documentation |
| `src/domains/agentverse/services/agent_service.py` | Mandatory creation guard and authenticated owner assignment |
| `src/domains/agentverse/entities/resource_exposure.py` | New typed exposure contract and shared legacy classification |
| `src/domains/agentverse/tools/spec.py` | Optional exposure and registration metadata propagation |
| `src/domains/agentverse/registries/tool_registry.py` | Spec propagation, exposure validation and shared catalog filtering |
| `src/domains/agentverse/registries/base.py` | Normalized exposure property; remove dead duplicate items method |
| `src/domains/agentverse/agents/factory.py` | Preserve runtime declarations and config precedence |
| `src/domains/agentverse/tests/services/test_creation_governance.py` | Direct/socket denial, valid admins, forged payloads, zero resource work |
| `src/domains/agentverse/tests/registries/test_tool_registration_governance.py` | Metadata equivalence, legacy flags/tags, internal catalog omission, factory propagation |
| `docs/REGISTRY_GOVERNANCE_V1_PROGRESS.md` | This implementation and remaining-gap report |

## Verification

All tests used the existing API Docker container, `python -m pytest -q -p no:cacheprovider`.

- **210 passed**: new creation tests, terminal failure envelopes, all registry and agent tests, invocation scope and tool-manager authorization, ToolSpec outputs, AgentToolRef keys.
- **250 passed**: V2 communication/history/cognitive response, agent lifecycle/session/UTU, NCSC authorization/CPBX, runtime compatibility, agent type keys, workflow attachment/key normalization and workflow service. Redis integration used randomized test namespaces in DB 15 derived from the existing container connection; no flush or credential output. No paid model calls.
- Both final suites reported 11 existing Pydantic deprecation warnings.
- New files: Ruff check and formatting pass. Full changed-file Ruff check reports **16 existing diagnostics** on unchanged code/file modes (EXE002, BLE001, S110, RUF010, RUF012); no auto-fixes applied to existing files.
- Mypy passes for the new exposure module with `--explicit-package-bases --follow-imports=silent`. This is a limited type check, not a whole-project claim. The first invocation without explicit package bases stopped on the repository's duplicate `domains.agentverse` / `src.domains.agentverse` naming.
- `git diff --check` passes.

An exploratory broader run also collected the existing `test_ws_route_teardown.py`: **12 failed, 160 passed, 91 skipped**. The failures concern fixtures lacking authenticated users and references to the absent `routes.presence` API. Both that test file and `interfaces/api/v1/websockets/routes.py` are byte-for-byte unchanged from HEAD (`git diff --quiet HEAD -- <both paths>` succeeds). They were not repaired as part of registry governance. The Redis-backed rerun above resolves the 91 integration skips for the selected V2/lifecycle suites. New authenticated creation tests for both socket handlers pass.

## Remaining stages and material risks

### RG-02 — not implemented

- Add UTU discover/read policy using a trusted principal and tenant. Legacy `User` currently has no tenant contract; do not trust a request tenant field.
- Apply policy to `RegistryService.list/get_info`, catalog/detail routes, personality/soul reads, registered and persisted workflows, and nested expansions.
- Restrict client-selectable registry kinds; `synaptic` and `tool_runtime_dependency` remain selectable today.
- Add explicit internal-agent exposure to persisted definitions and agent read/list policy, with per-tenant checks. Private visibility currently still allows owner/group/admin access and is not internal-only.
- Known-key reads still bypass catalog omission. RG-01 fixes top-level hidden/system tools appearing in the tool catalog; it does not secure direct metadata, soul or nested expansion access.
- Keep internal registry lookup/bootstrap independent of UTU discovery policy.

### RG-03 — not implemented

- Known-key tool attachment still primarily checks existence. Distinguish user attachments from trusted core/role/workflow enrichment and reject unauthorized internal/system attachments.
- Tool invocation policy remains inconsistent: workflow scope is restricted; system scope alone is not sufficient; system recognition uses tags. Propagating existing trusted tags now makes those declarations available, but does not complete execution policy.
- Prevent explicit attachments from overriding a role system tool's restriction in factory merging, while preserving workflow tools and authorized internal execution.
- Move internal-execution denial before `ensure_instance()` in system-minimal execution. Client context flags must not grant internal authority.
- Freeze the synaptic registry only after all static and conditional startup registration, including conditional `chat.response`; cover Genesis-enabled and disabled startup. No production freeze was added prematurely.

Until these stages are implemented and tested, catalog omission is not a confidentiality or invocation guarantee. No claim of complete Registry Governance V1 is made.
