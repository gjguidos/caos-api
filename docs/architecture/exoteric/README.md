# Exoteric — The Enterprise CAOS System

The Exoteric perspective describes CAOS as a practical, testable **Cognitive Agentic Operative System** for enterprise integrations, tools, agents and workflows. It complements the [Esoteric research perspective](../esoteric/README.md) on the **same system**; it is not another implementation, a purely deterministic product variant or a declaration that all content is public.

See [ADR-0002](../decisions/ADR-0002-exoteric-esoteric-knowledge.md) for the documentation principle and the [architecture knowledge index](../README.md) for canonical source/test paths and security-layer distinctions. Repository paths below are root-relative. The root `README.md` remains the platform vision, not a completed-feature inventory.

## AOSA and practical concepts

Use [AOSA Engineering Principles](AOSA_ENGINEERING_PRINCIPLES.md) as the canonical design/review reference before architectural changes. It defines engineering direction and the `#weather` delivery priority without treating proposed runtimes or research as implemented behavior.

**AOSA — Agent-Oriented System Architecture — is the architectural foundation; CAOS is the platform built around it.** Preserve explicit identity/authority, composition, shared execution contracts, domain ownership, dependency injection and bounded lifecycle behavior. Events coordinate services; tools and workflows need not use an LLM. Model-assisted results can be nondeterministic while authorization and lifecycle invariants remain explicit and testable.

| Concept | Practical distinction | Existing evidence / status |
| --- | --- | --- |
| Agent | Identity, configured behavior and authorized capabilities; composition is not unrestricted delegation | V1 `AgentService`/`AgentFactory` and distinct minimal `AgentFactoryV2`/`ManagedAgentV2`; see index before equating their responsibilities |
| Tool | A spec-described capability provider; declaration is separate from execution and grants | `src/domains/agentverse/tools/spec.py`, `src/domains/agentverse/tools/base.py`; use the existing ToolSpec skill |
| Workflow | Ordered composition, dependencies, input/output bindings and pause/resume behavior | Existing `WorkflowTool`/`WorkflowEngine` and [run contract](../../agentverse-workflow-run-contract.md); WFT V2 remains proposed |
| Operation | Durable execution identity, lifecycle, progress and outcome, distinct from workflow semantics | `src/base/operations/service.py`; existing service is not itself a general resource-authorization boundary or proof of OPS V2/Enlil |

Use `.agents/skills/aosa-tool-spec/SKILL.md`, `.agents/skills/aosa-registry/SKILL.md` and `.agents/skills/ncsc-cpbx/SKILL.md` for implementation procedures; do not duplicate them here.

## Governance, security, observability and lifecycle

- **Deterministic governance:** establish identity and approved authority from trusted context; apply default-deny checks at relevant entry points. A model, cognitive mode, symbolic name, registration or UI visibility cannot grant access. NCSC/CPBX enforces its supplied policy; UTU owns specific authority/permit decisions. Reuse the [existing security layers](../README.md#security-trace-the-layers-not-just-a-slogan), not a new security mechanism.
- **Resource boundaries:** authorization to invoke a capability is not blanket authorization for payload-named resources. Tenant/ownership and requester access remain path-specific; THOTH's general knowledge-resource checks are proposed, not universally implemented.
- **Observability:** correlate initiation, authorization, execution and outcomes without treating logs as unrestricted archives of sensitive data. Event/synaptic correlation is evidence of traceability, not proof of a complete platform-wide audit deployment.
- **Lifecycle:** verify setup, activation, session ownership, cancellation, revocation and release paths. Existing Genesis session/UTU and V1 socket tests are bounded evidence; neither implies the complete mythology is operational.

Concrete entry points include `src/domains/agentverse/ncsc/cpbx.py`, `src/domains/agentverse/security/utu.py`, `src/domains/agentverse/agents/lifecycle/service.py` and `src/domains/agentverse/tests/websockets/test_ws_route_teardown.py`. Follow the index for related wiring and tests. Do not claim tests ran merely because these files exist.

## Current implementation versus planned architecture

- **Observed baseline:** the index records source entry points reviewed at `56a2b9c5e170c343aa71869d4df118bc8490cf73`. Existing tools, local synaptic dispatch, event policies, operation/workflow support and Genesis lifecycle code must still be traced for the question at hand; source presence alone does not prove deployment.
- **Planned:** THOTH, its four cognitive modes, privileged knowledge tools and Enki-mediated provisioning are [ADR-0001 proposals](../decisions/ADR-0001-thoth-privileged-knowledge.md). Distributed Enlil execution/recovery, WFT V2 and a cognitive-homeostasis controller remain planned. [OPS2-04B](../../../src/base/operations/v2/README.md#ops2-04b--managed-aggregate-and-atomic-fencing) supplies bounded managed persistence/fencing, not the distributed runtime or a completed product slice.
- **Unknown or incomplete:** report missing wiring, resource checks, integration prerequisites, unexecuted tests and documentation drift rather than promoting intent to fact. Review current source instead of inferring capability from a branch name.

## Connecting research to engineering

The [Esoteric crosswalk](../esoteric/README.md#inspiration-to-engineering-crosswalk) explains possible motivations, not historical proof of implementation choices. Interpretive context suggests separating observations from interpretations; symbolic creation/destruction suggests explicit activation/cleanup boundaries; homeostasis suggests measurable bounded recovery. Each engineering claim still needs its own contracts and tests. Enterprise users need not accept these philosophical premises to rely on the verified system.
