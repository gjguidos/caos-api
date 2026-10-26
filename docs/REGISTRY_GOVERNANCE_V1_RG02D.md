# Registry Governance V1 — RG-02D Resource Ownership Governance

Date: 2026-10-26. Branch: `feature/ncsc-pbx`. HEAD remains `1207b1f`.

RG-02D is implemented on top of the existing uncommitted RG-00/RG-01/RG-02 work. No commit, push, deployment, production data migration, RG-03, Enki or weather work was performed. The earlier reports are historical snapshots; their statements that missing-tenant legacy records are global are superseded here.

## Ownership contract

Ownership is independent of exposure (`public`/`internal`) and agent visibility (`public`/`private`). Stored entities have an additive nullable `ownership` field; null/missing means **unclassified**, never global. The typed declaration values are:

| Ownership | Required declaration | Client access | Provisioning/mutation |
| --- | --- | --- | --- |
| `tenant` | Nonblank canonical `tenant_id` | Verified matching JWT/session tenant, then exposure/visibility rules | Client creation derives tenant from authenticated User; existing role/group checks remain |
| `global` | No tenant or tenant-scoped flag | Authenticated identity, then exposure/visibility rules | Trusted code/control plane only; client update/delete denied |
| `system` | No tenant or tenant-scoped flag | Denied even with public exposure or admin role | Trusted code/control plane only |
| Missing/invalid/conflicting | None inferred | Denied for lists, known keys and conversations | Preserved for explicit review |

An existing `tenant_id` alone does not classify ownership. Names, creator IDs, tags, exposure, builtin origin, runtime approvals, query parameters, headers and payload ownership establish no ownership authority. Whitespace tenant IDs are rejected rather than normalized into a guessed tenant.

## Verified flows and implementation

- HTTP JWT middleware and V1 WebSocket authentication already propagate `tid` after token verification into `User.tenant_id`; these files needed no RG-02D edits. V2 validates signed identity/tenant/session claims and the durable session binding independently. These authenticated boundaries remain unchanged.
- `resource_ownership.py` centralizes ownership validation and the content-free inventory projection. UTU applies it before exposure and visibility. Registry generic/raw APIs remain trusted and unchanged.
- Registry client catalogs and known-key reads require explicit ownership for each root resource and referenced record. Ordinary config/UI/step data inherits its enclosing scope; explicit inline ownership/tenant declarations and identifiable RegistryItem expansions require independent classification. A referenced legacy resource cannot borrow its parent's ownership. Existing schema exclusions, bounded traversal and whole-parent denial remain.
- Personality/soul expansion carries the already-authorized registration ownership context into ordinary soul data while enforcing nested restrictions.
- Agent reads/listing/update/delete check raw persisted policy before Pydantic can discard flags/default fields; the existing identity/visibility/group checks still apply. Mongo discovery filters include owned scopes, and the service rechecks every result. Invalid ownership cannot crash a catalog by reaching model parsing. Global agents are client read-only.
- Agent creation requires verified tenant and the RG-00 admin/group-admin predicate before duplicate reads, validation, factory construction or persistence. Payload ownership/tenant/exposure extras are removed before factory work, and the final entity is explicitly `tenant` owned with the verified tenant. PATCH also rejects ownership and tenant-scoped changes. Existing error/response envelopes and runtime/provenance restrictions remain.
- Workflow creation requires identified verified tenant before any resource work and authors tenant ownership. Client ownership/tenant extras do not survive create/update request serialization. Persisted reads use the same UTU policy; tenant updates/deletes remain scoped, while global/system/unknown records cannot be mutated by client paths. Trusted bulk lookup and runtime resolution remain independent.
- V1 cached chat still re-reads the governed agent definition. V2 conversation entry/detail/history checks ownership for every participant through UTU, including calls without an explicit target subset. Conversation identity, tenant and membership contracts are unchanged.
- New local Genesis Adam seeds explicitly declare tenant ownership under the existing local/dev gates and fixed trusted policy. Existing historical seeds are **not** assigned ownership. The existing trusted activation/runtime-provenance compatibility path is preserved independently from client reads. Trusted approval cannot make an unclassified participant readable in a conversation.
- New builtin workflow seeds explicitly declare shared/global ownership from the trusted builtin factory catalog. Startup preserves existing unknown, tenant-owned, system-owned or conflicting rows for review, with a warning. Refreshes require a conditional global/builtin query, preserve existing policy fields and do not retry a concurrent unique-key collision by key alone.

## Auditable inventory and migration plan

`src.domains.agentverse.security.ownership_inventory` is a **read-only** command over a trusted JSON-array snapshot. It emits source, SHA-256 of the exact input bytes, status counts and per-record key/ownership/tenant/status. It omits prompts, definitions, credentials and other content. It makes no database calls or assignments.

Example, after placing an authorized export inside the existing API container:

```bash
docker compose exec -T api python -m src.domains.agentverse.security.ownership_inventory /tmp/agents-export.json --source coll_agents
docker compose exec -T api python -m src.domains.agentverse.security.ownership_inventory /tmp/workflows-export.json --source coll_workflows
docker compose exec -T api python -m src.domains.agentverse.security.ownership_inventory /tmp/tools-export.json --source registry:tool
```

Persisted exports are document arrays; registry exports are `RegistryItem.model_dump()` arrays captured after the deployment's trusted registration initialization. Inventory every client registry kind as well as `coll_agents` and `coll_workflows`. Do not expose this administrative inventory as a client catalog. Protect exports and reports as administrative tenant metadata. No live Mongo/production inventory or production migration was run in this task.

1. Capture a consistent authorized backup/export, deployment/code revision, snapshot hash, operator, timestamp and row counts. Retain absent-versus-null fields and record revisions/modified timestamps in the protected source snapshot.
2. Generate the read-only inventory. Reconcile counts, missing/duplicate identifiers, references, conflicts and records absent from the export. Audit known Genesis IDs and builtin keys as records, without treating matching names, origins or approvals as ownership evidence.
3. Produce a separate review manifest: source, immutable ID/key, exact expected prior ownership/tenant field presence and revision/hash, proposed ownership/tenant, evidence, reviewer, rationale and ticket. Establish tenant from authoritative business/provisioning records. Explicitly approve shared resources; classify system resources separately from exposure. Leave uncertain rows unclassified.
4. Review registered code declarations separately and author ownership in trusted registration metadata only for approved resources. This implementation intentionally does **not** blanket-classify existing static catalogs or infer ownership from tags. Undeclared registrations are therefore unavailable to clients until reviewed. Recheck parent manifests and referenced tools/roles/personalities/rulepacks/workflows together.
5. Dry-run the reviewed manifest against a fresh snapshot. Reject stale, missing, duplicate or conflicting records; verify tenant existence and reference consistency. Global workflow keys and agent IDs retain their existing namespace/index rules: RG-02D does not introduce per-tenant duplicate keys.
6. In a separately authorized maintenance change, apply only the reviewed assignments with conditional compare-and-set updates matching immutable ID, exact prior field presence/value and record revision. Abort conflicts; never retry by key alone or assign a default tenant. Record actor, evidence, before/after policy and result for each operation. Preserve content, IDs, provenance and history; no deletions or bulk blanket assignments.
7. Re-inventory and verify same-tenant operations, cross-tenant denials, known-key denial, catalogs, V1 cached chat, V2 conversation/history, and fresh/existing Genesis/bootstrap behavior. Confirm row counts/content hashes apart from reviewed policy changes. Conditional rollback must require the expected post-migration revision to avoid erasing concurrent edits.

Until classification, historical Adam may still activate through an existing trusted approval but cannot enter client conversations; historical workflows remain stored and trusted runtime lookup remains available. This is intentional fail-closed behavior, not data loss. No executable write-migration utility or automatic startup ownership backfill was introduced.

## Verification

All test runs used the existing API Docker container, `python -m pytest -q -p no:cacheprovider`. Redis integration used the configured local connection with database 15, randomized fixture namespaces and targeted cleanup, never flush. No external/paid LLM calls were made.

- Initial focused checks exposed tests relying on the superseded implicit-global behavior; success fixtures were updated to explicit ownership/verified tenant. The new tests retain explicit unclassified-denial cases. A first broader command had an incorrect test directory and collected nothing; the corrected final invocation below completed.
- Final combined suite: **839 passed, no skips**, 6 existing Pydantic deprecation warnings. Covers new RG-02D ownership matrix/inventory tests, RG-02 catalogs, RG-00 creation (both V1 sockets), workflow CRUD, all registry tests, V2 communication/history/cognitive responses, full lifecycle/session/UTU/revocation tests, authenticated V1 socket middleware, runtime compatibility, workflow validation/seeding/specs, agent composition, tool scope/authorization, workflow tool tests, file services/validation and workflow attachment/key normalization. This is a selected broad compatibility suite, not whole-repository pytest.
- New ownership policy, inventory command and RG-02D tests pass Ruff check and formatting. Targeted service/security/seed/request formatting was applied; no repository-wide auto-fix was run.
- Ruff comparison of affected Python files against their exact start-of-task contents (or HEAD for previously clean files): **18 baseline diagnostics, 18 current, no added diagnostic signatures/counts**. Existing executable-file and unrelated style warnings remain.
- Limited mypy passes with `--explicit-package-bases --follow-imports=silent` for ownership policy, inventory command, registry service and personality service. No whole-project mypy claim.
- `git diff --check` passes. The incoming work was snapshotted before editing; files outside the RG-02D list below remain byte-for-byte unchanged from that snapshot. HEAD and branch are unchanged.

## Remaining caveats

- Unclassified persisted **and registered** resources are deliberately unavailable to clients until explicitly reviewed. Existing clients may see smaller/empty catalogs; this is the requested fail-closed policy, not a data migration. No tenant is guessed, including for historical Genesis seeds.
- Trusted Python registry/factory/bulk workflow/principal-resolution APIs retain internal functionality. They must remain inaccessible to arbitrary clients. Existing activation approvals intentionally remain distinct from client resource ownership; this does not harden hostile in-process Python code.
- Existing endpoint names, error objects, V1/V2 envelopes and composition contracts remain; `ownership` is additive on persisted entity serialization. New permission denials and readonly global resources are intentional policy changes. No browser/Next.js end-to-end test was run.
- RG-03 invocation/attachment governance, override protections and synaptic freeze remain open and were not started. Ownership read checks grant no execution capability.
- New opaque reference formats and content embedded in arbitrary prose need explicit adapters/classification. Inventory reports do not automatically prove all business ownership claims or nested-reference completeness.

## Exact RG-02D changed files

The list below is relative to this checkout and compared with the **start of this task**, not merely HEAD. Shared RG-00/RG-01/RG-02 files retain their incoming work. Existing documentation is marked with a superseding note rather than rewritten.

- `docs/REGISTRY_GOVERNANCE_V1_PROGRESS.md`
- `docs/REGISTRY_GOVERNANCE_V1_RG02.md`
- `docs/REGISTRY_GOVERNANCE_V1_RG02D.md`
- `src/domains/agentverse/agents/lifecycle/tests/test_genesis_local.py`
- `src/domains/agentverse/dependencies/genesis_local.py`
- `src/domains/agentverse/entities/agent.py`
- `src/domains/agentverse/entities/agent_update_request.py`
- `src/domains/agentverse/entities/resource_ownership.py`
- `src/domains/agentverse/entities/workflow.py`
- `src/domains/agentverse/security/agent_access.py`
- `src/domains/agentverse/security/ownership_inventory.py`
- `src/domains/agentverse/security/utu.py`
- `src/domains/agentverse/services/agent_service.py`
- `src/domains/agentverse/services/personality_service.py`
- `src/domains/agentverse/services/registry_service.py`
- `src/domains/agentverse/services/workflow_service.py`
- `src/domains/agentverse/tests/communication/test_cognitive_response_v2.py`
- `src/domains/agentverse/tests/communication/test_communication_v2.py`
- `src/domains/agentverse/tests/services/test_catalog_governance.py`
- `src/domains/agentverse/tests/services/test_creation_governance.py`
- `src/domains/agentverse/tests/services/test_resource_ownership.py`
- `src/domains/agentverse/tests/services/test_workflow_service.py`
- `src/domains/agentverse/tests/workflows/test_seed_builtins.py`
- `src/domains/agentverse/workflows/seed.py`
