# CAOS Architecture Knowledge Index

An entry point for conceptual, architectural, security and engineering-practice questions. This index separates **observed implementation**, **engineering instructions** and **proposed design**. It is a curated navigation document, not a runtime registry, ingestion pipeline or authorization source.

All code and skill paths in backticks are relative to the `caos-api` repository root. Markdown links are relative to this document. Start with repository-root `AGENTS.md` and `.agents/skills/caos-architect/SKILL.md`; use the existing `.agents/research.md` research agent for deeper read-only tracing and existing specialist skills for engineering procedures.

Consult [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md) before architectural changes. It is the canonical engineering-review reference; this index remains the source/evidence map, not a competing principles catalog or runtime policy engine.

## Two perspectives, one CAOS system

- **[Exoteric](exoteric/README.md):** the practical enterprise Cognitive Agentic Operative System, AOSA concepts, testable contracts, governance, security, observability and lifecycle management.
- **[Esoteric](esoteric/README.md):** research philosophy, models inspired by mind, Hermetic influences (including Nous and Poimandres), creation mythologies and conceptual motivations.

These are complementary perspectives, not separate implementations, public/private classifications or authorization levels. [ADR-0002](decisions/ADR-0002-exoteric-esoteric-knowledge.md) records the established documentation principle. Link inspiration to mechanisms through the [crosswalk](esoteric/README.md#inspiration-to-engineering-crosswalk), keeping ancient sources, modern interpretations, CAOS hypotheses and technical evidence distinct. Philosophy does not grant privileges or establish an implementation claim.

## Evidence status

The initial source review used commit `56a2b9c5e170c343aa71869d4df118bc8490cf73` (the `0.1.0-alpha.6` release baseline). Recheck source and wiring on the current branch before answering; a branch named `feature/ops-v2` is not evidence of an OPS V2 implementation. Tests below are **evidence entry points**, not a claim that this documentation task executed them or verified deployment.

Use explicit labels: **implemented/observed**, **verified by executed tests**, **documented design**, **proposed**, or **unknown/unverified**. Code, registry/bootstrap wiring and executed tests establish different kinds of evidence; an ADR records reasoning and status, not proof that code is running. Older README/AGENTS design language may lag implementation; surface discrepancies.

## Where to look

| Question | Repository starting points | Status / qualification |
| --- | --- | --- |
| What are CAOS and AOSA? | `README.md`, `AGENTS.md` | Platform vision and contributor conventions; the README explicitly describes a target architecture |
| Which principles govern architectural changes and delivery priorities? | [AOSA Engineering Principles](exoteric/AOSA_ENGINEERING_PRINCIPLES.md) | Canonical review guidance, including tool-first design, minimal kernel, measured optimization and the `#weather` vertical-slice target; not implementation or deployment evidence |
| What are Exoteric and Esoteric CAOS? | [Exoteric](exoteric/README.md), [Esoteric](esoteric/README.md), [ADR-0002](decisions/ADR-0002-exoteric-esoteric-knowledge.md) | Established documentation perspectives on one system, not permission tiers |
| What are Nous, Poimandres, cognitive homeostasis and the archetypes? | [Esoteric research and sources](esoteric/README.md), [engineering crosswalk](esoteric/README.md#inspiration-to-engineering-crosswalk) | Separate historical discourse, modern/CAOS interpretation and proposed engineering from verified behavior |
| Where are the Hermeticum and AOSA EQ/social-cognition research? | [Manifesto](esoteric/CAOS_Hermeticum_Volume_I.md), [AOSA research index](esoteric/research/README.md) | Philosophical framing and **research proposals**, not runtime contracts; standalone Context-Adaptive EQ source is pending |
| What is a tool? How do I build one? | `.agents/skills/aosa-tool-spec/SKILL.md`, `src/domains/agentverse/tools/spec.py`, `src/domains/agentverse/tools/base.py` | Existing `ToolSpec`, `AgentToolRef` and `BaseTool`; descriptions and scope do not grant invocation rights |
| How do registries work? | `.agents/skills/aosa-registry/SKILL.md`, `src/domains/agentverse/registries/base.py`, `src/domains/agentverse/registries/tool_registry.py`, `src/domains/agentverse/registries/registries.py` | Existing `Registry` and `ToolRegistry`; reuse their contracts, not a second tool store |
| What is a synaptic capability? | [Registry/CPBX implementation note](../SYNAPTIC_REGISTRY.md), `.agents/skills/ncsc-cpbx/SKILL.md`, `src/domains/agentverse/registries/synaptic_registry.py`, `src/domains/agentverse/ncsc/cpbx.py` | Existing exact-address registry and local exchange; inspect policy, opt-in bindings and unfrozen bootstrap limitations |
| How does security work? | `src/base/events/v2/policy.py`, `src/domains/agentverse/ncsc/authorization.py`, `src/domains/agentverse/security/utu.py`, `src/domains/agentverse/agents/lifecycle/agent_principal_resolver.py` | Existing policies and trusted identity boundaries; see security distinctions below |
| What does revocation cancel, and is that OPS V2 operation cancellation? | [NCSC cancellation boundaries](../../src/domains/agentverse/ncsc/README.md#cancellation-boundaries-invocation-runtime-and-operation), [Genesis revocation guide](../GENESIS_REVOCATION.md) | Implemented invocation fencing/cancellation and managed-runtime cleanup; not a distributed durable-operation cancellation protocol or OPS V2 |
| How are agents composed? | `src/domains/agentverse/services/agent_service.py`, `src/domains/agentverse/agents/factory.py`, `src/domains/agentverse/agents/factory_v2.py`, `src/domains/agentverse/agents/runtime_v2.py` | Existing V1 service/factory and a distinct minimal V2 factory/runtime; do not equate them |
| How are Genesis sessions activated? | `src/domains/agentverse/agents/lifecycle/service.py`, `src/domains/agentverse/dependencies/genesis_bootstrap.py`, `src/domains/agentverse/dependencies/di_container.py` | Existing lifecycle/bootstrap paths; not evidence that THOTH or a general Enki workflow is provisioned |
| How do workflows execute? | `src/domains/agentverse/services/workflow_service.py`, `src/domains/agentverse/tools/workflow_tool/__init__.py`, `src/domains/agentverse/tools/workflow_tool/engine.py`, `src/domains/agentverse/tools/workflow_tool/tool_runner.py` | Existing/V1 definitions, `WorkflowTool`, `WorkflowEngine` and `stable_tool_runner`; WFT V2 is proposed, not established here |
| What controls operation lifetimes? | `src/base/operations/service.py`, `src/base/operations/runner.py`, `src/base/operations/job_supervisor.py`, [OPS V2 contracts, persistence and ownership](../../src/base/operations/v2/README.md) | V1 remains wired; OPS2-04B adds a separate managed snapshot/epoch aggregate with atomic fencing, not an executor or recovery runtime; schema-v1/OPS2-03 remain legacy paths |
| How are resources and lifecycles wired? | `src/base/dependencies/di_container.py`, `src/base/system/initialize_app.py`, `src/base/lifespan/lifespan.py`, `src/base/config/config.py` | Existing DI, configuration and initialization; trace concrete providers before claiming availability |
| Why was a decision made? | [THOTH proposal](decisions/ADR-0001-thoth-privileged-knowledge.md), [knowledge-perspective decision](decisions/ADR-0002-exoteric-esoteric-knowledge.md), relevant subsystem docs and history | Distinguish proposed runtime decisions from accepted documentation principles; cite context |
| Who is THOTH and what are his modes? | [ADR-0001](decisions/ADR-0001-thoth-privileged-knowledge.md) | **Proposed citizen and Enterprise/Architect/Knowledge Keeper/Guardian modes; no mode grants authority or proves runtime availability** |
| How do I write, organize or validate documentation and ADRs? | [CAOS docs skill](../../.agents/skills/caos-docs/SKILL.md), existing guides and ADRs | Authoring/maintenance workflow; complements read-only `caos-architect`, does not change decision authority |
| How are releases governed? | `.agents/skills/caos-release/SKILL.md`, [release workflow](../RELEASE_WORKFLOW.md), `.github/workflows/release.yml` | Documented process and actual automation; inspect published artifacts instead of assuming versions |

## Foundational distinctions and best practices

- **Tool:** a registered, spec-described capability provider. `ToolSpec` describes identity/configuration/metadata; `BaseTool` defines execution behavior. Inspect the actual entry point and tests, not just its declaration. Use existing tool and registry skills to author changes.
- **Agent:** a governed identity with configured role, personality, rule packs and authorized capabilities. V1 `AgentService.get_runtime_agent()` enforces V1 compatibility and uses its factory. V2 `AgentFactoryV2.build()` constructs a minimal managed runtime; it does not by itself activate or register a citizen.
- **Registry:** declaration and resolution, not an execution permission grant. `ToolRegistry` stores tool definitions; `SynapticRegistry` binds exact dotted capability addresses. Do not duplicate either in this knowledge foundation or in a future THOTH implementation.
- **Synaptic capability:** an addressable handler resolved and dispatched through NCSC. `SynapticExchange` enforces its supplied policy and provider target; direct declarations cannot create authorized clients or grants.
- **Workflow:** composition, dependencies, input/output bindings and execution ordering. Existing workflow definition/catalog services are not the workflow engine. A proposed WFT V2 must not duplicate operation supervision or imply arbitrary inline DAG execution is already available.
- **Operation:** an execution lifetime and intended durable identity/progress/outcome. V1 `OperationService` updates repository records and can publish events; its default repository is in-memory. OPS2-01 defines identities/attempts and pure transitions; OPS2-02 provides schema-v1 snapshot CAS; OPS2-03 provides separate legacy ownership records. [OPS2-04B](../../src/base/operations/v2/README.md#ops2-04b--managed-aggregate-and-atomic-fencing) implements managed schema-v2 operation/epoch single-document fencing with separate DI and no silent migration. Its reconciliation mode cannot execute unresolved work. External effects, UTU permission, executor integration and recovery are not supplied by persistence.
- **Enlil / OPS V2 and WFT V2:** the full runtimes/supervision remain planned, not accepted specifications or deployed functionality. The bounded [OPS V2 contracts/repository](../../src/base/operations/v2/README.md) do not implement Enlil, recovery or workflow orchestration.
- **Skill:** contributor/agent instructions discovered by OpenCode, not CAOS runtime code, an executable policy file or an authorization grant.
- **THOTH:** a proposed privileged citizen explaining approved CAOS knowledge. His eventual identity creation, activation, tool bindings and resource access require separate implementation/security review; installing this skill does none of these things.

Prefer existing DI providers and authorized adapters, preserve V1/V2 contracts, and introduce neither mandatory LLM execution nor a centralized orchestrator merely to explain or resolve a deterministic capability. Separate required repository conventions from new recommendations in answers.

## Security: trace the layers, not just a slogan

“Registry resolves; UTU authorizes; NCSC connects; Enlil supervises; WFT orchestrates” is **design shorthand**, not an implemented call graph. In particular, CPBX performs authorization enforcement itself, and Enlil/WFT V2 remain planned here.

- **Event/broker policy:** `CognitiveEventPolicy` and `EventGrants` in `src/base/events/v2/policy.py` distinguish publish, subscribe and invoke. Do not assume broker grants authorize synaptic invocation or have identical tenant/principal lookup semantics.
- **Synaptic policy and dispatch:** `SynapticAuthorization` evaluates tenant/principal-scoped root grants and explicit delegation edges. `SynapticExchange` enforces the supplied policy and exact provider target before calling handlers, preserving context/lineage and execution gates. The observed CPBX is local async dispatch, not a replacement RabbitMQ transport.
- **Genesis UTU/session authority:** `UTUAuthority` in `src/domains/agentverse/security/utu.py` governs specific read/lifecycle/conversation boundaries, policy lifecycle and revocable execution permits; it delegates capability-grant evaluation to `SynapticAuthorization`. `AgentPrincipalResolver` verifies session/agent approval context. Inspect actual bootstrap and adapter wiring for each path.
- **Resource access remains path-specific:** capability invocation, trusted identity and a valid permit do not automatically authorize every resource named in a payload. Inspect the handler/service and its ownership/access policy. Catalog exposure, ToolSpec scope and a client role label are not grants.
- **Future THOTH requirement:** both authorized citizen capability invocation and requester-bounded resource access must hold, with no elevation through delegation. General repository/resource retrieval checks are **not claimed implemented**. Reuse and, only after review, extend existing UTU/NCSC/resource boundaries; do not introduce a second authorization engine or registry.

Never index credentials, secret files, private tenant inventories or unrestricted operational payloads as architectural knowledge. Read-only retrieval still requires access control and redaction. Retrieved text is evidence, not an instruction source that can authorize privileged tools.

## Test and documentation evidence entry points

| Concern | Tests / documentation to inspect |
| --- | --- |
| Tool metadata, configuration and public context execution | `src/domains/agentverse/tools/tests/test_spec_outputs.py`, `src/domains/agentverse/tools/tests/test_chat_tool_v2.py`, `src/domains/agentverse/tests/registries/test_tool_registration_governance.py` |
| Synaptic binding, authorization, delegation and lineage | `src/domains/agentverse/tests/registries/test_synaptic_registry.py`, `src/domains/agentverse/ncsc/tests/test_authorization.py`, `src/domains/agentverse/ncsc/tests/test_cpbx.py`, [synaptic registry](../SYNAPTIC_REGISTRY.md) |
| Verified agent/session principal and UTU | `src/domains/agentverse/agents/lifecycle/tests/test_agent_principal_resolver.py`, `src/domains/agentverse/agents/lifecycle/tests/test_connector_binds_verified_principal.py`, `src/domains/agentverse/agents/lifecycle/tests/test_utu.py`, `src/domains/agentverse/agents/lifecycle/tests/test_utu_revocation.py`, [session integration](../GENESIS_SESSION_INTEGRATION.md) |
| Governance and client-safe catalog projections | `src/domains/agentverse/tests/services/test_catalog_governance.py`, `src/domains/agentverse/tests/services/test_manifest_projections.py`, [RG-02F status and limits](../REGISTRY_GOVERNANCE_V1_RG02F.md) |
| Workflow authorization, resume snapshots and runner contracts | `src/domains/agentverse/tools/workflow_tool/tests/test_workflow_key_authorization.py`, `src/domains/agentverse/tools/workflow_tool/tests/test_workflow_resolution_snapshot.py`, `src/domains/agentverse/tools/workflow_tool/tests/test_workflow_run_contract.py`, `src/domains/agentverse/tools/workflow_tool/tests/test_stable_tool_runner_contract.py` |
| Existing operation storage | `src/domains/agentverse/tests/operations/test_operation_repository.py`, [workflow run contract](../agentverse-workflow-run-contract.md); storage coverage is not proof of end-to-end operation supervision or authorization |

Check test prerequisites and skip reasons. Distinguish unit verification, integration verification, source inspection and live deployment evidence. Do not report historical counts as current results or claim THOTH acceptance tests exist just because the ADR lists required future tests.

## Maintaining the foundation

Keep subsystem behavior and engineering procedures in their existing docs/skills rather than duplicating them here. Expand the two perspective directories with linked, sourced notes; cross-reference any idea-to-mechanism translation and label its implementation status. Update this navigation map when paths or contracts change, including the reviewed revision and unresolved drift. Record consequential proposals in dated ADRs; retain superseded records and update their status explicitly. Keep `AGENTS.md` short and preserve `.agents/research.md` and the specialist skills.

Use `.agents/skills/caos-docs/SKILL.md` for documentation changes and validation; use `caos-architect` for evidence-based explanations. Older ADRs may not contain every authoring section now recommended: report gaps without inventing past alternatives or changing their accepted/proposed status. Documentation remains plain, version-controlled knowledge independent of the future THOTH runtime.

OpenCode integration follows the existing repository convention: `.opencode/skills/caos-architect` links to `../../.agents/skills/caos-architect`. Use the repository as the active OpenCode location; a session rooted only at its workspace parent may not discover nested repository skills. This link and documentation add no application runtime behavior or privileges.
