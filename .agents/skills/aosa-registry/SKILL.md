---
name: aosa-registry
description: Design, implement, inspect and test registries in the CAOS AgentVerse domain. Use when modifying Registry[T], AgentRegistry, ToolRegistry, WorkflowRegistry, registration decorators, registry instances, or NCSC SynapticRegistry capability bindings.
---

# AOSA Registry Skill

## Purpose

Maintain AOSA's existing modular registry architecture.

Prefer reusing established infrastructure over introducing new registration, discovery or component-management mechanisms.

Read the repository's `AGENTS.md` before making changes.

## 1. Architecture boundaries

CAOS separates generic infrastructure from AgentVerse-specific functionality.

- `src/base/` contains reusable infrastructure.
- `src/domains/agentverse/` contains AOSA agents, tools, registries, workflows and NCSC-specific implementations.
- NCSC PBX belongs to AgentVerse, not to the generic base layer.

Do not introduce dependencies from `src/base` into `src.domains.agentverse`.

## 2. Inspect existing implementations

Before creating or modifying a registry, inspect:

- `src/domains/agentverse/registries/base.py`
- The existing Agent, Tool and Workflow registries
- `register_registry`
- The domain's registry-instance initialization and exports
- Relevant tests and registry consumers

For NCSC work, additionally inspect:

- `src/base/events/v2/registry.py`
- `src/base/events/v2/runtime.py`
- `src/base/events/v2/contracts.py`
- `src/base/events/v2/policy.py`

Confirm the actual filenames and interfaces in the working repository. Do not invent APIs based on documentation alone.

## 3. Registry conventions

Use the existing `Registry[T]` where its semantics fit.

Preserve its established capabilities:

- Decorator-based registration
- Canonical component keys
- Component validation
- Registry metadata
- Version-aware resolution
- Lookup caching
- Cache invalidation
- Catalog integration
- Development reload compatibility

Specialized registries should extend the generic registry with only their domain-specific requirements.

Follow existing `@register_registry(...)` conventions where applicable.

Avoid duplicating component stores or creating alternative sources of truth.

## 4. Keys and addressing

Distinguish component keys from event addresses.

Component registries use canonical keys.

NCSC signals use dotted addresses, such as:

- `personality.infuse`
- `context.delta.build`
- `prompt.build`
- `agent.action.chat`
- `tool.trigger.chat`

Preserve event addresses exactly.

Before using the generic registry's canonicalization logic, verify that it cannot introduce address collisions.

If internal storage keys differ from public signal addresses, implement explicit reversible or collision-free resolution.

## 5. SynapticRegistry

`SynapticRegistry` is an AgentVerse registry used by NCSC PBX.

Its responsibilities are:

- Declare executable capability bindings
- Register handlers through decorators
- Resolve capability bindings
- Validate registration metadata
- Detect conflicting provider registrations
- Expose discovery metadata where appropriate

Its responsibilities do not include:

- Executing handlers
- Granting permissions
- Managing RabbitMQ connections
- Performing cognitive reasoning
- Coordinating workflows
- Constructing agents

These belong to their respective runtime components.

Use the existing `CognitiveEventRegistry` for broker event contracts and subscriptions. Do not duplicate its authority.

## 6. Registration security

The generic AOSA registry supports last-write-wins behavior to accommodate module reloads.

A security-sensitive synaptic registry must distinguish legitimate refreshes from provider conflicts.

Never silently replace an existing signal binding with an unrelated provider.

Avoid accepting runtime handler registration from untrusted requests.

Prefer deterministic registration during application startup.

Registration does not imply execution authorization.

## 7. Authorization and execution

NCSC PBX uses trusted identity and policy enforcement.

`SynapticExchange` is responsible for authorized request dispatch and correlation.

Reuse existing NCSC authorization and event contracts wherever possible.

Maintain:

- Caller and provider identities
- Tenant isolation
- Capability permissions
- Scoped delegation
- Correlation and causation
- Failure propagation

Do not allow a caller to grant itself authority through event payloads.

Local dispatch must enforce security policies just as distributed dispatch does.

## 8. Decorator design

Prefer simple declarative registration:

```python
@synaptic_registry.subscribe(
    "personality.infuse",
    provider="tool.personality",
)
async def infuse_personality(event, context):
    ...
```

Decorators should register handlers without executing them.

Do not instantiate tools, agents or external resources during registration unless the existing registry lifecycle explicitly requires it.

Keep handlers thin when adapting existing AOSA components.

## 9. V1/V2 compatibility

CAOS is introducing NCSC PBX without immediately removing its existing execution infrastructure.

Preserve current:

- BaseAgent composition
- AgentService lifecycle
- ToolSpec and ToolRegistry
- AgentFactory behavior
- Legacy ChatAgent and LLM invoker
- WebSocket contracts

Introduce V2 implementations only where execution responsibilities genuinely change.

Prefer adapters and shared construction logic over copying entire implementations.

Do not remove UniversalOrchestrator dependencies from existing components until the replacement path has been tested.

## 10. Testing requirements

For registry changes, verify:

1. Successful decorator registration
2. Exact address and key resolution
3. Duplicate and conflicting registration behavior
4. Supported reload behavior
5. Metadata consistency
6. Invalid component rejection
7. Unknown registration lookup
8. Compatibility with existing registry consumers

For NCSC execution, additionally verify:

- Authorization is enforced
- Tenant boundaries are respected
- Handler failures propagate correctly
- Results maintain correlation information
- V1 execution remains unaffected

Prefer focused unit tests before broader integration tests.

## 11. Working procedure

When modifying a registry:

1. Read `AGENTS.md`.
2. Inspect existing registry implementations.
3. Identify existing functionality that can be reused.
4. Explain the proposed registration and lookup contracts.
5. Implement the smallest compatible change.
6. Add focused tests.
7. Run tests and relevant lint/type checks.
8. Report changed files, outcomes and remaining risks.

Challenge proposed abstractions when existing components already solve the problem.

## Guiding principle

**AOSA defines capabilities. Registries describe and resolve them. Adapters expose them. NCSC PBX connects them.**

Keep the implementation modular, secure, backward-compatible and as simple as possible.

## Repository skills

Skills are maintained in `.agents/skills/`.

Do not duplicate skill definitions under `.opencode/skills/`.
Use the existing skills before creating new ones.
