---
name: ncsc-pbx
description: Design, implement, review and test the NCSC Cognitive PBX, including SynapticExchange, authorized synaptic clients, synaptic invocation contracts, capability adapters, and AOSA cognitive tool integration.
---

# NCSC PBX Skill

## Purpose

Develop the Cognitive Private Branch Exchange (CPBX) as the communication mechanism of the Neural Cognitive Synapse Center (NCSC).

NCSC enables authorized communication between AOSA agents, tools, workflows, teams and cognitive capabilities.

Read the repository's root `AGENTS.md` before making changes. Use the `aosa-registry` skill for registry-specific modifications.

## Architecture

Preserve the following boundaries:

- **AOSA:** Defines agents, roles, personalities, rule-packs, tools and cognitive resources.
- **SynapticRegistry:** Registers and resolves addressable capability bindings.
- **Synapse:** Represents an individual communication instance.
- **SynapticExchange:** Authorizes and dispatches capability requests.
- **Adapters:** Connect synaptic requests to existing AOSA implementations.
- **Workflows:** Coordinate multi-step execution when required.

NCSC must not become a UniversalOrchestrator, workflow engine or LLM inference engine.

## Repository ownership

Keep generic infrastructure under `src/base`.

Place NCSC-specific functionality under:

`src/domains/agentverse/ncsc/`

Use existing registry infrastructure under:

`src/domains/agentverse/registries/`

Do not introduce a second message broker, event registry or dependency-injection framework.

## Existing infrastructure

Before modifying CPBX, inspect:

- `src/base/events/v2/contracts.py`
- `src/base/events/v2/policy.py`
- `src/base/events/v2/registry.py`
- `src/base/events/v2/runtime.py`
- `src/domains/agentverse/registries/synaptic_registry.py`
- `src/domains/agentverse/ncsc/models.py`
- `src/domains/agentverse/ncsc/cpbx.py`

Verify which components actually exist in the working branch.

## Synaptic invocation

A synaptic request identifies:

- Signal
- Authenticated source
- Target provider
- Tenant
- Payload
- Synapse ID
- Correlation ID
- Causation ID

Preserve these values across nested capability requests.

Signal identifiers are exact dotted addresses, for example:

- `personality.infuse`
- `context.delta.build`
- `prompt.build`
- `agent.action.chat`

## Authorization

Default deny.

Use trusted principal-bound clients and existing NCSC policy infrastructure.

Distinguish `publish`, `subscribe` and `invoke` permissions.

Registration never grants authorization.

Do not accept caller identity, tenant identity or permission grants from untrusted payloads.

Prevent implicit privilege inheritance in nested requests.

Check target and tenant access separately from signal-level invocation permission.

## Dispatch

Prefer local invocation for in-process operations.

Use existing RabbitMQ infrastructure when distributed messaging is required.

Keep handler registration separate from execution.

Propagate handler failures explicitly.

Do not introduce mandatory LLM reasoning or workflow execution for simple capability requests.

## Cognitive tools

Prefer existing AOSA ToolSpec, BaseTool and ToolRegistry conventions.

Cognitive tools may implement operations such as:

- `personality.infuse`
- `context.delta.build`
- `prompt.build`
- `response.format`

Preserve the existing responsibilities of LLM context providers.

An agent may invoke authorized tools without owning their concrete instances.

Keep agent-specific configuration separate from provider implementation and capability grants.

## Personality Tool

The initial integration should expose the existing personality resolver as an authorized synaptic capability.

Do not redesign Agent Soul or persist personality changes as part of infusion.

Use the existing personality model and resolver.

Test that synaptic invocation produces equivalent results to direct personality resolution.

## Migration

Preserve V1 implementations while introducing V2 execution paths.

Do not change BaseAgent, AgentFactory, ChatAgent or UniversalOrchestrator unless the current task explicitly requires it.

Prefer thin adapters and reuse existing lifecycle and construction services.

Maintain existing WebSocket contracts and client commands.

## Testing

Verify:

1. Authorized capability invocation succeeds.
2. Missing invocation grants are denied.
3. Publish-only grants cannot invoke capabilities.
4. Unknown capabilities fail explicitly.
5. Target/provider mismatches are rejected.
6. Correlation and causation are preserved.
7. Handler failures propagate.
8. Existing registries and event tests remain functional.
9. Existing V1 behavior remains unchanged.

Add focused tests before broad integration tests.

## Working procedure

1. Inspect actual code and existing tests.
2. Identify the smallest compatible implementation.
3. Explain changes to architectural boundaries.
4. Implement one capability or communication mechanism.
5. Run focused tests and lint checks.
6. Report results and compatibility risks.
7. Do not commit without explicit approval.

## Guiding principle

**AOSA defines capabilities. SynapticRegistry registers them. Adapters expose them. NCSC PBX connects them.**
