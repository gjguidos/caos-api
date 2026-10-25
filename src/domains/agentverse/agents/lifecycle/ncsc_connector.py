from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)


class NCSCConnectionError(RuntimeError):
    pass


class VerifiedAgentPrincipal(Protocol):
    @property
    def agent_id(self) -> str: ...

    @property
    def tenant_id(self) -> str: ...


class AuthorizedNCSCClient(Protocol):
    def revoke(self) -> None: ...

    async def drain(self) -> None: ...

    async def request(
        self,
        signal: str,
        payload: dict,
        *,
        target: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> object: ...


class SynapticExchangeBinding(Protocol):
    def bind(
        self,
        principal: VerifiedAgentPrincipal,
    ) -> AuthorizedNCSCClient: ...


AgentPrincipalResolver = Callable[
    [AgentInstanceKey],
    Awaitable[VerifiedAgentPrincipal],
]


class TrustedNCSCConnector:
    """
    Trusted activation-time bridge to NCSC.

    Only system-owned factory and lifecycle components
    should receive this connector.

    Permission grants are resolved externally through
    a trusted principal resolver.
    """

    def __init__(
        self,
        *,
        exchange: SynapticExchangeBinding,
        resolve_principal: AgentPrincipalResolver,
    ) -> None:
        self._exchange = exchange
        self._resolve_principal = resolve_principal

    async def __call__(
        self,
        key: AgentInstanceKey,
    ) -> AuthorizedNCSCClient:
        principal = await self._resolve_principal(key)

        if principal is None:
            raise NCSCConnectionError("Agent principal resolution failed")

        if principal.agent_id != key.agent_id:
            raise NCSCConnectionError("Agent identity mismatch")

        if principal.tenant_id != key.tenant_id:
            raise NCSCConnectionError("Agent tenant mismatch")

        client = self._exchange.bind(principal)

        if client is None:
            raise NCSCConnectionError("NCSC binding failed")

        return client
