# SynapticRegistry v0.1 and Implemented NCSC/CPBX

**Status:** Observed implementation, reconciled against source at `8cf05979ac22b3131791b7aabc3d34ef20ff90d1`. Source inspection is not deployment verification. Repository paths below are root-relative; Markdown links are document-relative.

**Scope:** Exact-address registration, local capability dispatch, current discovery and bootstrap boundaries. This updates the original registry-only milestone description; it does not introduce new code, grants or security policy.

AgentVerse owns this capability-binding registry. It reuses AOSA `Registry[T]`
storage, metadata, cache invalidation, version checks and catalog integration.
It does not execute handlers, authorize callers, construct resources, register
broker contracts or subscribe to RabbitMQ. `CognitiveEventRegistry` remains the
authority for broker contracts and subscriptions. A separate, implemented
`SynapticExchange` in `src/domains/agentverse/ncsc/cpbx.py` now provides local
async dispatch through an `AuthorizedSynapticClient`; this is not a replacement
RabbitMQ transport or an authorization function of the registry itself.

The following remains a declaration example, not a built-in capability or grant:

```python
from src.domains.agentverse.registries import synaptic_registry_instance
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient

@synaptic_registry_instance.subscribe("prompt.build", provider="tool.prompt")
async def build_prompt(synapse: Synapse, client: AuthorizedSynapticClient):
    ...

binding = synaptic_registry_instance.resolve("prompt.build", version="1.0.0")
```

Handlers must be async functions. CPBX calls
`await binding.handler(synapse, child_client)`; registry validation checks that
the handler is async but does not validate its parameter signature. `Synapse`
carries the signal, trusted source/tenant, provider target, copied payload and
lineage. Payloads must be dictionaries; handler results are currently `Any`,
not a universal typed result envelope. See `src/domains/agentverse/ncsc/models.py`
and `src/domains/agentverse/ncsc/cpbx.py` for the actual contracts.

## Addressing and discovery

All public keys are exact lowercase dotted signals, including `get`, `get_info`,
`resolve_key`, membership, `keys`, `items`, `list`, and catalog summaries. No key
canonicalization is applied: `a.bc` and `ab.c` remain distinct. Unknown valid
addresses raise `KeyError` on resolution; invalid addresses raise `ValueError`.
Versions use AOSA's single-current-version check, not multiversion storage.
Provider and version strings must be nonempty and have no surrounding whitespace.

The domain singleton and trusted `RegistryService` methods expose registry
metadata to runtime/bootstrap consumers. The generic **client catalog excludes
the synaptic registry entirely**: `CLIENT_KINDS`, `list_for_client()` and
`get_info_for_client()` in
`src/domains/agentverse/services/registry_service.py` enforce that boundary.
Allowed catalog kinds receive ownership/exposure-aware projections; this is
separate from capability invocation grants. Do not put secrets in metadata.

## Implemented dispatch and authority

Trusted runtime code constructs `SynapticExchange(registry=..., authorization=...)`
and binds an approved principal. Public/agent callers use the resulting
`AuthorizedSynapticClient.request(signal, payload, ...)`, not registry mutation
or client-supplied identity. `bind()` trusts its in-process caller; it is not
an independent authentication API or a sandbox for hostile Python code.

CPBX enters the execution gate and supplied policy context, resolves the exact
signal, rejects a provider-target mismatch, copies the payload and invokes the
handler with an exchange-issued child client. Nested calls preserve original
principal/tenant, permit, gate and correlation while advancing causation.
`SynapticAuthorization` in `src/domains/agentverse/ncsc/authorization.py` checks
tenant/principal root grants and every trusted delegation edge, rejects recursive
or over-depth paths, and does not treat broker grants as synaptic grants.

The default Genesis DI composition in
`src/domains/agentverse/dependencies/di_container.py` supplies **UTUAuthority** as
the exchange policy and uses the trusted principal resolver/connector. This path
adds execution-permit, session/revocation and before/after execution checks plus
in-flight cancellation through `src/domains/agentverse/security/utu.py`.
Standalone exchanges using bare `SynapticAuthorization` do not acquire those
Genesis permit guarantees merely by using the same exchange class.
Capability permission still does not authorize every resource named in a payload;
handlers/services retain resource-specific checks. See the
[architecture security map](architecture/README.md#security-trace-the-layers-not-just-a-slogan).

## Existing bindings and opt-in startup

- `chat.context` → `tool.chat`: import-time declaration in
  `src/domains/agentverse/tools/chat_tool_v2/synaptic.py`. It constructs a fresh
  `ChatToolV2` and calls `provide_context()` with supplied text/history; it is
  context-only, not LLM inference or per-agent tool-instance resolution.
- `chat.response` → `cognitive.chat`: the startup hook in
  `src/domains/agentverse/dependencies/genesis_bootstrap.py` registers
  `CognitiveResponseV2.handle` when a configured grant includes `chat.response`.
  The governed handler checks permit/session/definition/history access before
  invoking a configured model; it is not general tool/workflow orchestration.
- `build_genesis_security()` is opt-in via `GENESIS_SESSIONS_ENABLED=true`.
  Missing approvals/grants deny by default. Loading the optional server-owned
  policy file is not Redis tenant-policy provisioning. The existing local Adam
  helper has additional local/development restrictions; it is not a general
  deployment provisioning path.

Importing the registry singleton alone does not import every adapter. Generic
application discovery attempts module imports but logs errors and continues;
that behavior is not a fail-closed trusted binding bootstrap. Inspect the actual
startup path and configuration before claiming a binding is available.

## Trusted registration lifecycle

The registry supports the previously documented secure-registration sequence:
explicit trusted imports, fail-closed registration and
`synaptic_registry_instance.freeze()` before exposing dispatch. **Current
production wiring does not automatically perform or enforce that complete
sequence.** No production freeze call was found in the reviewed source;
Genesis startup can register `chat.response` late. The exchange does not require
a frozen registry. Freeze support is implemented; deterministic import/register/
freeze enforcement remains integration work, not a completed safeguard.

Do not expose registry mutation to requests or agents, and do not describe generic
router discovery as a secure substitute for explicit registration.

Before freezing, repeated registration from the same provider and handler
`(__module__, __qualname__)` refreshes the binding and caches. A leading `src.`
module alias is ignored for legacy imports. A different provider or function
origin conflicts, including direct `register()` calls. Legitimate module reloads
are supported only during this mutable development/bootstrap phase. Function
origin is a trusted-code convention, not cryptographic provider authentication.

Freezing is irreversible and blocks registration and removal, including previously
created decorators. Rebuild/restart a frozen registry to change bindings. Python
objects are not a security sandbox. Runtime identity, tenant context, correlation
and invocation policy are now enforced at the implemented exchange and trusted
Genesis authority boundaries described above, not inferred from registration.

## Compatibility

The preliminary implementation's hex-encoded catalog keys are replaced by exact
signals. Consumers persisting those experimental keys must migrate to signals.
Generic AOSA registries retain canonical keys and last-write-wins behavior.
Existing V1 execution remains a separate compatibility path. V1 `AgentFactory`
hydrates synaptic ToolSpec metadata; V2 adds a separate minimal managed runtime
and trusted NCSC connector. Metadata hydration neither migrates V1 agents nor
grants execution authority. Existing event/broker infrastructure remains distinct
from local CPBX invocation.

## Evidence and verification limits

Relevant test entry points (presence alone is not a passing run):

- `src/domains/agentverse/tests/registries/test_synaptic_registry.py` — exact keys,
  conflicts/reloads, trusted discovery and freeze mutation protection.
- `src/domains/agentverse/ncsc/tests/test_cpbx.py` and
  `src/domains/agentverse/ncsc/tests/test_authorization.py` — dispatch, targets,
  tenant/principal grants and nested lineage/delegation.
- `src/domains/agentverse/tests/services/test_catalog_governance.py` — exclusion
  of synaptic metadata from client discovery while trusted resolution remains.
- `src/domains/agentverse/tools/tests/test_chat_tool_v2.py` — context behavior and
  scoped authorization of the actual adapter.
- `src/domains/agentverse/agents/lifecycle/tests/test_utu_revocation.py` and
  `src/domains/agentverse/agents/lifecycle/tests/test_connector_binds_verified_principal.py`
  — permit/revocation and trusted identity boundaries; check integration prerequisites.

This note does not claim deployed availability, general resource authorization,
automatic registry freezing or implementation of proposed capabilities such as
the illustrative `prompt.build`. Philosophical or symbolic descriptions never
override these technical limits.
