
from __future__ import annotations

from src.domains.agentverse.agents.runtime_v2 import (
    ManagedAgentV2,
    NCSCConnector,
)
from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)


class AgentFactoryV2:
    """
    Builds minimal CAOS-managed runtimes.

    Does not activate, register, or claim ownership.
    """

    def __init__(
        self,
        *,
        ncsc_connector: NCSCConnector,
    ) -> None:
        self._ncsc_connector = ncsc_connector

    async def build(
        self,
        key: AgentInstanceKey,
    ) -> ManagedAgentV2:
        return ManagedAgentV2(
            key=key,
            ncsc_connector=self._ncsc_connector,
        )
