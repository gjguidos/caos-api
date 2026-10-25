from __future__ import annotations

from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)

from .models import AgentLifecycleResult
from .service import AgentLifecycleService


class Anubis:
    """
    Trusted CAOS agent deactivation supervisor.

    Responsible for graceful sleep and, later,
    finalization of failed runtimes.
    """

    def __init__(
        self,
        *,
        lifecycle: AgentLifecycleService,
    ) -> None:
        self._lifecycle = lifecycle

    def revoke_local(
        self, tenant_id: str, *, agent_id: str | None = None, session_id: str | None = None
    ) -> None:
        """Trusted system shutdown after UTU revocation; no caller session required."""
        self._lifecycle.revoke_local(tenant_id, agent_id, session_id)

    async def sleep(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        return await self._lifecycle.deactivate(key)

    async def status(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        return await self._lifecycle.status(key)
