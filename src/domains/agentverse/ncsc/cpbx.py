from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.ncsc.authorization import (
    SynapticPolicy,
)
from src.domains.agentverse.ncsc.models import (
    Synapse,
    SynapticExecutionContext,
)
from src.domains.agentverse.registries.synaptic_registry import (
    SynapticRegistry,
)


class ExecutionGate:
    def __init__(self) -> None:
        self.revoked = False
        self.tasks: dict[asyncio.Task[Any], int] = {}

    def revoke(self) -> None:
        if self.revoked:
            return
        self.revoked = True
        for task in tuple(self.tasks):
            task.cancel()

    async def drain(self) -> None:
        tasks = tuple(self.tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=2.0)
            if pending:
                raise RuntimeError("CPBX work has not quiesced")

    @asynccontextmanager
    async def execution(self) -> AsyncIterator[None]:
        if self.revoked:
            raise EventPermissionError("CPBX runtime gate revoked")
        task = asyncio.current_task()
        assert task is not None
        self.tasks[task] = self.tasks.get(task, 0) + 1
        try:
            yield
            if self.revoked:
                raise EventPermissionError("CPBX runtime gate revoked")
        finally:
            self.tasks[task] -= 1
            if not self.tasks[task]:
                del self.tasks[task]


class AuthorizedSynapticClient:
    """Client bound to a trusted synaptic execution context."""

    def __init__(
        self,
        exchange: SynapticExchange,
        context: SynapticExecutionContext,
    ) -> None:
        self._exchange = exchange
        self._context = context

    def revoke(self) -> None:
        self._context.gate.revoke()

    async def drain(self) -> None:
        await self._context.gate.drain()

    async def request(
        self,
        signal: str,
        payload: dict[str, Any],
        *,
        target: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Any:
        return await self._exchange._request(
            context=self._context,
            signal=signal,
            payload=payload,
            target=target,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


class SynapticExchange:
    """NCSC CPBX: authorized capability communication."""

    def __init__(
        self,
        *,
        registry: SynapticRegistry,
        authorization: SynapticPolicy,
    ) -> None:
        self._registry = registry
        self._authorization = authorization

    def bind(
        self,
        principal: EventPrincipal,
    ) -> AuthorizedSynapticClient:
        """Issue an agent client from trusted runtime code only."""

        context = SynapticExecutionContext(
            principal_id=principal.agent_id,
            tenant_id=principal.tenant_id,
            permit=getattr(principal, "permit", None),
            gate=ExecutionGate(),
        )
        return AuthorizedSynapticClient(self, context)

    async def _request(
        self,
        *,
        context: SynapticExecutionContext,
        signal: str,
        payload: dict[str, Any],
        target: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Any:
        async with (
            context.gate.execution(),
            self._authorization.execution(context=context, signal=signal),
        ):
            if not isinstance(payload, dict):
                raise TypeError("Synaptic payload must be a dictionary")

            binding = self._registry.resolve(signal)

            destination = binding.provider if target is None else target

            if destination != binding.provider:
                raise EventPermissionError(f"Target denied for signal {signal!r}")

            # Nested requests retain their execution lineage.
            kwargs: dict[str, Any] = {
                "signal": signal,
                "source": context.principal_id,
                "target": destination,
                "tenant_id": context.tenant_id,
                "payload": deepcopy(payload),
                "causation_id": (
                    context.causation_id if context.active_signal is not None else causation_id
                ),
            }

            effective_correlation = (
                context.correlation_id if context.active_signal is not None else correlation_id
            )

            if effective_correlation is not None:
                kwargs["correlation_id"] = effective_correlation

            synapse = Synapse(**kwargs)

            # CPBX issues the context for the receiving capability.
            child_context = SynapticExecutionContext(
                principal_id=context.principal_id,
                tenant_id=context.tenant_id,
                active_signal=signal,
                correlation_id=synapse.correlation_id,
                causation_id=synapse.synapse_id,
                invocation_chain=(*context.invocation_chain, signal),
                permit=context.permit,
                gate=context.gate,
            )

            child_client = AuthorizedSynapticClient(
                self,
                child_context,
            )

            return await binding.handler(synapse, child_client)
