from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.domains.agentverse.agents.lifecycle.ncsc_connector import AuthorizedNCSCClient
from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)

NCSCConnector = Callable[
    [AgentInstanceKey],
    Awaitable[Any],
]


class AgentRuntimeInactiveError(RuntimeError):
    pass


class ManagedAgentV2:
    """
    Minimal CAOS-managed agent runtime.

    It owns no LLM, tools, WebSocket, or autonomous loop yet.
    """

    def __init__(
        self,
        *,
        key: AgentInstanceKey,
        ncsc_connector: NCSCConnector,
    ) -> None:
        self._key = key
        self._ncsc_connector = ncsc_connector
        self._synaptic_client: Any | None = None
        self._active = False
        self._revoked = False
        self._lock = asyncio.Lock()

    @property
    def id(self) -> str:
        return self._key.agent_id

    @property
    def user_id(self) -> str:
        return self._key.user_id

    @property
    def tenant_id(self) -> str:
        return self._key.tenant_id

    @property
    def session_id(self) -> str:
        return self._key.session_id

    @property
    def is_active(self) -> bool:
        return self._active

    # Add inside ManagedAgentV2.

    @property
    def ncsc(self) -> AuthorizedNCSCClient:
        if not self._active or self._synaptic_client is None:
            raise AgentRuntimeInactiveError("Agent is not connected to an active runtime")

        return self._synaptic_client

    async def connect(self) -> None:
        async with self._lock:
            if self._synaptic_client is not None:
                return

            client = await self._ncsc_connector(self._key)

            if client is None:
                raise RuntimeError("NCSC connection could not be established")

            if self._revoked:
                client.revoke()
                raise AgentRuntimeInactiveError("Runtime was revoked during connection")
            self._synaptic_client = client

    async def start(self) -> None:
        async with self._lock:
            if self._synaptic_client is None:
                raise RuntimeError("Cannot activate an agent without NCSC")

            if self._revoked:
                raise AgentRuntimeInactiveError("Runtime revoked")
            self._active = True

    def revoke(self) -> None:
        """
        Immediately prevent new runtime operations.

        Called by the trusted lifecycle supervisor.
        """
        self._active = False
        self._revoked = True
        if self._synaptic_client is not None:
            self._synaptic_client.revoke()

    async def stop(self) -> None:
        self.revoke()

        async with self._lock:
            if self._synaptic_client is not None:
                await self._synaptic_client.drain()
            self._synaptic_client = None

    async def assert_active(self) -> None:
        if not self._active or self._synaptic_client is None:
            raise AgentRuntimeInactiveError(f"Agent {self.id} is not active")

    async def heartbeat(self) -> dict[str, str]:
        """
        Genesis-only smoke-test operation.

        This is not Redis lease renewal.
        """
        await self.assert_active()

        return {
            "agent_id": self.id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "status": "active",
        }
