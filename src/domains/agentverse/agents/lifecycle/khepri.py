
from __future__ import annotations

from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)

from .models import AgentLifecycleResult
from .service import AgentLifecycleService


class Khepri:
    """
    Trusted CAOS agent activation supervisor.

    Responsible for awakening and, later, recovery.
    """

    def __init__(
        self,
        *,
        lifecycle: AgentLifecycleService,
    ) -> None:
        self._lifecycle = lifecycle

    async def awaken(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        return await self._lifecycle.activate(key)

    async def status(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        return await self._lifecycle.status(key)
