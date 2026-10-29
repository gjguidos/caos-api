---
name: aosa-tool-spec
description: Design, extend, review and validate AOSA ToolSpec contracts, including tool configuration, metadata hydration, agent references, workflow inputs and outputs, cognitive capabilities, and NCSC synaptic declarations.
---

# AOSA ToolSpec

## Purpose

Maintain ToolSpec as the declarative contract for tools in Agent-Oriented System Architecture (AOSA).

ToolSpec describes a tool's identity, configuration, capabilities, invocation scope, supported inputs, outputs, and metadata.

It must remain independent of tool execution, agent orchestration, authorization decisions, and transport infrastructure.

Before making changes, read the repository's root `AGENTS.md` and inspect the actual implementation.

## Source of Truth

Primary implementation:

`src/domains/agentverse/tools/spec.py`

Related systems:

- ToolRegistry and tool metadata
- AgentToolRef and agent configuration
- AgentFactory tool enrichment
- Workflow tool execution and output bindings
- Tool invocation and runtime dependencies
- NCSC SynapticRegistry and SynapticAuthorization

Use the `aosa-registry` skill when modifying registry behavior.

Use the `ncsc-cpbx` skill when modifying synaptic routing or authorization.

## Architectural Principles

1. ToolSpec describes a tool. It does not execute the tool.
2. ToolRegistry owns trusted tool definitions and their metadata.
3. AgentToolRef stores a lightweight reference and agent-specific configuration.
4. AgentFactory resolves references into runtime ToolSpecs.
5. Tool configuration must remain separate from security authority.
6. Tools can expose capabilities without requiring agents to own concrete tool instances.
7. New ToolSpec fields must preserve compatibility with existing tools.
8. Prefer typed optional declarations over new managers, registries, or configuration engines.

## Existing ToolSpec Contract

Preserve existing fields and semantics:

- `key`, `name`, `description`, `tool_class`, `version`
- `scope`
- `metadata`, `config`
- `tags`, `enabled`
- `ui`, `outputs`, `supports`
- `source`
- `synaptic`

Do not silently change existing serialization, canonicalization, metadata hydration, or default behavior.

### Tool Scope

`agent`: Tool is available through the agent's direct invocation surface.

`workflow`: Tool is equipped to satisfy workflow requirements and is not automatically exposed for direct agent invocation.

`system`: Tool is provided for system-level responsibilities.

ToolScope is not an NCSC permission grant. Preserve the distinction.

## AgentToolRef

AgentToolRef stores:

- Canonical tool key
- Agent-specific configuration

Do not duplicate the full ToolSpec on the persisted agent reference.

Do not allow AgentToolRef configuration to override trusted capability declarations, permissions, or approved Trusted Lines.

## Metadata Hydration

ToolSpec supports deriving certain typed fields from trusted registry metadata.

Follow existing Pydantic v2 patterns using `field_validator`.

For fields hydrated from metadata when omitted, use `validate_default=True` where required.

Preserve explicit field values over metadata-derived defaults.

Validate structured metadata instead of silently accepting malformed capability declarations.

Remember that Pydantic field validators only have reliable access to previously validated fields.

When extending ToolSpec, check field ordering and test both explicit initialization and metadata hydration.

## Synaptic Capabilities

ToolSpec may declare NCSC capabilities using:

```python
class SynapticCapabilitySpec(BaseModel):
    signal: str
    access: Literal["core", "granted", "system"] = "granted"
    trusted_lines: list[str] = Field(default_factory=list)


class SynapticToolSpec(BaseModel):
    capabilities: list[SynapticCapabilitySpec] = Field(
        default_factory=list
    )
```

Synaptic declarations describe capabilities and requested dependencies.

They do not create executable handlers or grant authorization.

### Signals

Use exact dotted signal identifiers.

Examples:

- `chat.respond`
- `personality.infuse`
- `context.delta.build`
- `prompt.build`
- `llm.generate`
- `data.validation.deterministic`

Validate signals using the same addressing contract as SynapticRegistry.

Do not canonicalize or silently rewrite signal identifiers into different addresses.

### Access

`core`: Capability is eligible for default provisioning to approved agents.

`granted`: Capability requires explicit authorization.

`system`: Capability is restricted to approved system execution.

The `access` declaration is availability metadata, not an authorization decision.

Untrusted tool configuration must not elevate a capability to core or system access.

### Trusted Lines

Trusted Lines describe approved capability dependencies.

Declare downstream dependencies on the individual capability that requests them.

Example:

```python
SynapticCapabilitySpec(
    signal="chat.respond",
    access="core",
    trusted_lines=[
        "personality.infuse",
        "llm.generate",
    ],
)
```

This describes:

- `chat.respond` → `personality.infuse`
- `chat.respond` → `llm.generate`

Do not place ambiguous tool-wide Trusted Lines in a specification containing multiple independent capabilities.

A declared dependency must be approved by trusted runtime configuration before authorization can use it.

## Tool Registry Integration

ToolRegistry registers tool definitions.

SynapticRegistry registers executable capability handlers.

Do not merge these responsibilities.

A ToolSpec may declare `chat.respond`, but that declaration does not automatically prove the corresponding handler exists.

During trusted startup, validate that approved and enabled synaptic capabilities have matching registered handlers.

Reject conflicting provider ownership and duplicate signal declarations.

Do not create a second capability registry.

## AgentFactory Integration

AgentFactory expands tool references using trusted registry metadata.

Preserve existing agent-specific configuration overrides without allowing them to alter trusted synaptic declarations.

Core capabilities should be derived from approved tool definitions, not from arbitrary client-provided specifications.

Preserve legacy core-tool behavior until the replacement execution path is tested.

Avoid broad changes to agent construction for isolated ToolSpec modifications.

## Workflow Compatibility

ToolSpec already supports workflow-related contracts, including:

- Typed output declarations
- Output paths into tool results
- File input slots
- Supported MIME types
- Workflow-only tool scopes
- UI metadata

Changes must not break existing workflow bindings or execution behavior.

Do not confuse workflow availability with direct agent invocation permission.

## Model Design

Prefer Pydantic models for declarative, serializable specifications.

Use:

- Explicit field types
- `Field(default_factory=...)` for mutable defaults
- Literal or Enum values for bounded classifications
- Focused validators
- Optional fields for backward-compatible extensions

Avoid untyped configuration dictionaries when a stable, reusable contract exists.

Avoid introducing new abstraction layers for one-off properties.

Keep runtime context, active sessions, handlers and authorization state outside ToolSpec.

## Security Boundaries

ToolSpec is declarative configuration, not an authorization engine.

Never derive effective execution permissions from untrusted agent overrides, user payloads or arbitrary plugin metadata.

Separate:

1. Declared capabilities
2. Approved capabilities
3. Provisioned grants
4. Runtime authorization

Trusted Lines must not grant standalone execution authority.

Authorization remains the responsibility of NCSC.

Tools performing sensitive operations must still enforce tenant and resource access restrictions.

## Compatibility Rules

When modifying ToolSpec:

1. Preserve existing field names unless an explicit migration is required.
2. Keep tools without synaptic metadata functional.
3. Preserve existing ToolScope behavior.
4. Preserve AgentToolRef serialization.
5. Preserve metadata hydration.
6. Preserve workflow output and file contracts.
7. Avoid introducing required fields into existing persisted specifications.
8. Maintain compatibility with Pydantic v2 and Python 3.11+.

## Testing

Add focused tests for:

- ToolSpec construction with and without synaptic declarations
- Explicit synaptic configuration
- Metadata hydration
- Invalid dotted signals
- Invalid Trusted Line destinations
- Core, granted and system access values
- Independence of agent configuration overrides
- Multiple capabilities with distinct Trusted Lines
- Existing ToolScope behavior
- Existing workflow output and file support
- Backward-compatible serialization and validation

When changes touch AgentFactory or ToolRegistry, run their existing relevant tests as well.

## Development Procedure

1. Inspect the current ToolSpec, ToolRegistry and relevant tests.
2. Identify the smallest necessary contract change.
3. Verify where registry metadata enters ToolSpec.
4. Implement typed fields and validation.
5. Add focused tests.
6. Verify backward compatibility.
7. Run relevant pytest, Ruff and mypy checks.
8. Report changed files, test results and compatibility risks.
9. Do not commit unless explicitly requested.

## Design Principle

**ToolSpec describes what a tool is and what capabilities it exposes. ToolRegistry manages its definition. SynapticRegistry connects its operations. NCSC governs their execution.**
