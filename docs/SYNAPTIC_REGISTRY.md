# SynapticRegistry v0.1

AgentVerse owns this capability-binding registry. It reuses AOSA `Registry[T]`
storage, metadata, cache invalidation, version checks and catalog integration.
It does not execute handlers, authorize callers, construct resources, register
broker contracts or subscribe to RabbitMQ. `CognitiveEventRegistry` remains the
authority for broker contracts and subscriptions. No SynapticExchange is added.

```python
from src.domains.agentverse.registries import synaptic_registry_instance

@synaptic_registry_instance.subscribe("prompt.build", provider="tool.prompt")
async def build_prompt(event, context):
    ...

binding = synaptic_registry_instance.resolve("prompt.build", version="1.0.0")
```

This is a declaration example, not a built-in capability or permission grant.
Handlers must be async functions; the future execution adapter owns their call
signature and request/result contracts.

## Addressing and discovery

All public keys are exact lowercase dotted signals, including `get`, `get_info`,
`resolve_key`, membership, `keys`, `items`, `list`, and catalog summaries. No key
canonicalization is applied: `a.bc` and `ab.c` remain distinct. Unknown valid
addresses raise `KeyError` on resolution; invalid addresses raise `ValueError`.
Versions use AOSA's single-current-version check, not multiversion storage.
Provider and version strings must be nonempty and have no surrounding whitespace.

The existing domain singleton and registry service expose discovery metadata.
Discovery is not authorization; current generic catalog endpoints do not filter
synaptic metadata by capability grants. Do not put secrets in that metadata.

## Trusted registration lifecycle

1. Trusted bootstrap imports an explicit, deterministic list of binding modules.
2. Registration failures must abort that bootstrap.
3. Call `synaptic_registry_instance.freeze()` before exposing capability dispatch.

This version supplies the lifecycle API, not application capability bootstrap or
automatic freezing: there are no built-in bindings or exchange to activate yet.
Do not rely on generic router discovery as a secure bootstrap; it logs import
failures and may continue. Do not expose registry mutation to requests or agents.

Before freezing, repeated registration from the same provider and handler
`(__module__, __qualname__)` refreshes the binding and caches. A leading `src.`
module alias is ignored for legacy imports. A different provider or function
origin conflicts, including direct `register()` calls. Legitimate module reloads
are supported only during this mutable development/bootstrap phase. Function
origin is a trusted-code convention, not cryptographic provider authentication.

Freezing is irreversible and blocks registration and removal, including previously
created decorators. Rebuild/restart a frozen registry to change bindings. Python
objects are not a security sandbox; runtime identity, tenant isolation, scope,
correlation and execution policy belong at the future exchange boundary.

## Compatibility

The preliminary implementation's hex-encoded catalog keys are replaced by exact
signals. Consumers persisting those experimental keys must migrate to signals.
Generic AOSA registries retain canonical keys and last-write-wins behavior.
BaseAgent, AgentFactory, ChatAgent and existing event infrastructure are unchanged.
