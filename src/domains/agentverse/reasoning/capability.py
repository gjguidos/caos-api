"""``cognitive.interpret@1.0.0``: Enki's governed reasoning capability (REASONING-0.1B).

Approved for Enki only (reviewed local UTU policy), provider ``system.reasoning``, no
trusted line. Executable only under its own single-signal attenuated scope, so an unscoped
client (including the generic signal route) or another capability's scope cannot reach it.
The caller must be an Agent in a live human session: tenant, human requester and source
Agent come only from the CPBX-issued, session-origin permit and UTU's trusted session
adapter; bootstrap permits are refused. Current authority (permit, policy versions, grant)
is re-verified before the model call and again before a result is released, so a result
computed for a requester or Agent whose authority ended meanwhile is never returned.

The provider runs the in-process ``ReasoningExecutor`` on published, fingerprint-pinned
tasks only. It never uses its CPBX child client, holds no Store, Genesis, Khepri, approval,
Manuscript or draft handle, and records no Seshat event. The result authorizes nothing.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.reasoning.executor import ReasoningExecutor
from src.domains.agentverse.reasoning.models import (
    ReasoningContext,
    ReasoningFailure,
    ReasoningResult,
)
from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

SIGNAL = "cognitive.interpret"
PROVIDER = "system.reasoning"
VERSION = "1.0.0"
BINDING = CapabilityBinding(SIGNAL, PROVIDER, VERSION)
SCOPE = CapabilityScope(SIGNAL, BINDING, frozenset({BINDING}))

# The caller's absolute ``time.monotonic()`` deadline. Set only by the trusted in-process
# caller (``interpret``) around one request; never readable from a payload. CPBX awaits the
# handler in the caller's task, so the value is visible to the provider.
_DEADLINE: ContextVar[float | None] = ContextVar("reasoning_deadline", default=None)


class ReasoningCapability:
    def __init__(
        self,
        *,
        authority: UTUAuthority,
        executor: ReasoningExecutor,
        definitions: Any = None,
    ) -> None:
        self._authority = authority
        self._executor = executor
        # Read-only Agent definitions, used only for the per-Agent model preference.
        self._definitions = definitions

    async def interpret(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        permit = await self._authorize(synapse, client)
        payload = synapse.payload
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, str) or task not in PUBLISHED_FINGERPRINTS:
            # Only published, fingerprint-pinned tasks are executable here.
            return self._failed("reasoning.task_unavailable")
        # The human is resolved from the trusted session adapter, never the payload.
        user = await self._authority.resource_requester(permit)
        if not user.id:
            raise EventPermissionError("Reasoning requires a verified human requester")
        context = ReasoningContext(
            tenant_id=permit.key.tenant_id,
            requester_id=user.id,
            correlation_id=synapse.correlation_id or synapse.synapse_id,
            source_agent_id=permit.key.agent_id,
            deadline=_DEADLINE.get(),
            preferred_model=await self._preferred_model(permit),
        )
        result = await self._executor.run(payload, context=context)
        if result.task_fingerprint not in (None, PUBLISHED_FINGERPRINTS[task]):
            return self._failed("reasoning.task_unavailable")
        # Never release a result once the requester's or Agent's authority has ended.
        await self._current(permit)
        return result.model_dump(mode="json")

    async def _authorize(
        self, synapse: Synapse, client: AuthorizedSynapticClient
    ) -> ExecutionPermit:
        """Independent provider-side verification of the exact scoped, session-origin caller."""
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if (
            not isinstance(permit, ExecutionPermit)
            or (synapse.signal, synapse.target, synapse.tenant_id, synapse.source)
            != (SIGNAL, PROVIDER, permit.key.tenant_id, permit.key.agent_id)
            or permit.scope != SCOPE
            or not permit.binding_chain
            or permit.binding_chain[-1] != BINDING
            or synapse.entry_operation != SCOPE.operation
            or synapse.provider_version != VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError(f"{SIGNAL} requires its own verified scoped caller")
        await self._current(permit)
        return permit

    async def _current(self, permit: ExecutionPermit) -> None:
        await self._authority.check_permit(permit)
        current = await self._authority.resolve_agent_principal(permit.key)
        if (
            current.permit.versions != permit.versions
            or SIGNAL not in self._authority.grants_for(current).invoke
        ):
            raise EventPermissionError("Reasoning authority changed")

    async def _preferred_model(self, permit: ExecutionPermit) -> str | None:
        """The source Agent's configured llm key; honoured only inside the allow-list."""
        if self._definitions is None:
            return None
        try:
            definition = await self._definitions.find_by_id(permit.key.agent_id)
        except Exception:  # noqa: BLE001 - a preference is optional; the default applies
            return None
        if not isinstance(definition, dict) or (
            definition.get("id"),
            definition.get("tenant_id", permit.key.tenant_id),
        ) != (permit.key.agent_id, permit.key.tenant_id):
            return None
        preferred = definition.get("llm_type")
        return preferred if isinstance(preferred, str) else None

    @staticmethod
    def _failed(code: Any) -> dict[str, Any]:
        return ReasoningResult(
            status="failed", failure=ReasoningFailure(code=code, retryable=False)
        ).model_dump(mode="json")


def reasoning_policy_state(policy: Any) -> bool:
    """Whether the reviewed policy enables ``cognitive.interpret``; trusted lines are refused."""
    if any(line.target == SIGNAL for line in policy._trusted_lines):
        raise ValueError(f"{SIGNAL} is approved without trusted lines")
    return any(SIGNAL in grant.invoke for grant in policy._grants.values())


def register_reasoning_capability(
    registry: SynapticRegistry, capability: ReasoningCapability
) -> SynapticBinding:
    """Trusted bootstrap registration of the exact signal. Grants nothing."""
    return registry.register(
        SIGNAL,
        description="Bounded interpretation through a published reasoning task; advisory only",
        version=VERSION,
        metadata={
            "access": "system",
            "deterministic": False,
            "read_only": True,
            "synaptic": SynapticToolSpec(
                capabilities=[SynapticCapabilitySpec(signal=SIGNAL, access="system")]
            ).model_dump(),
        },
        component=SynapticBinding(SIGNAL, PROVIDER, capability.interpret, VERSION),
    )


def validate_reasoning_binding(registry: SynapticRegistry) -> None:
    """Fail closed at trusted startup when the policy grants ``cognitive.interpret``."""
    try:
        binding = registry.resolve(SIGNAL, VERSION)
    except KeyError as exc:
        raise ValueError(f"Missing exact binding: {SIGNAL}@{VERSION}") from exc
    if (binding.provider, binding.version) != (PROVIDER, VERSION):
        raise ValueError(f"{SIGNAL} binding provider/version mismatch")


async def interpret(
    authority: UTUAuthority,
    exchange: SynapticExchange,
    principal: AuthorizedAgentPrincipal,
    request: dict[str, Any],
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Trusted in-process caller for an Agent runtime (Enki's future turn plan): narrows
    the verified principal to exactly ``cognitive.interpret`` and bounds it by ``deadline``
    (absolute ``time.monotonic()``)."""
    scoped = await authority.attenuate(principal, SCOPE)
    client = exchange.bind(scoped)  # NEVER bind the unattenuated principal.
    token = _DEADLINE.set(deadline)
    try:
        if deadline is None:
            return await client.request(SIGNAL, request, target=PROVIDER, version=VERSION)
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            return await client.request(SIGNAL, request, target=PROVIDER, version=VERSION)
    finally:
        _DEADLINE.reset(token)
        client.revoke()
        await client.drain()
