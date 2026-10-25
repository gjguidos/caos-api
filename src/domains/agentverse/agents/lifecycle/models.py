from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)


class AgentLifecycleState(StrEnum):
    CREATED = "created"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    SLEEPING = "sleeping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentLifecycleResult:
    key: AgentInstanceKey
    state: AgentLifecycleState
    already_active: bool = False


class ManagedAgentRuntime(Protocol):
    """
    Contract of an agent constructed by Factory V2.

    connect() establishes its authorized NCSC connection.
    start() activates runtime-owned resources.
    stop() quiesces execution and releases resources.
    """

    id: str
    user_id: str
    tenant_id: str

    def revoke(self) -> None: ...

    async def connect(self) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ManagedAgentFactory(Protocol):
    async def build(
        self,
        key: AgentInstanceKey,
    ) -> ManagedAgentRuntime: ...
