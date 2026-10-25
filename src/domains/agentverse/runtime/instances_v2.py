
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentInstanceKey:
    tenant_id: str
    user_id: str
    agent_id: str
    session_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id",
            "user_id",
            "agent_id",
            "session_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"AgentInstanceKey.{field_name} must be non-empty"
                )


class AgentInstanceRegistryV2:
    """
    Process-local registry of active AOSA agent instances.

    Responsibilities:
      - Runtime instance lookup
      - Identity-safe registration
      - Ownership-safe removal
      - Per-instance local synchronization

    Does not:
      - Spawn agents
      - Manage Redis presence
      - Authorize lifecycle operations
      - Coordinate distributed workers
    """

    def __init__(self) -> None:
        self._map: dict[AgentInstanceKey, object] = {}
        self._locks: dict[AgentInstanceKey, asyncio.Lock] = {}

    def get(self, key: AgentInstanceKey) -> object | None:
        return self._map.get(key)

    def register(
        self,
        key: AgentInstanceKey,
        agent: object,
    ) -> None:
        if (
            getattr(agent, "id", None) != key.agent_id
            or getattr(agent, "user_id", None) != key.user_id
        ):
            raise ValueError("Agent identity mismatch")

        if getattr(agent, "tenant_id", None) != key.tenant_id:
            raise ValueError("Agent tenant mismatch")

        if getattr(agent, "session_id", None) != key.session_id:
            raise ValueError("Agent session mismatch")

        existing = self._map.get(key)

        if existing is agent:
            return

        if existing is not None:
            raise RuntimeError(
                f"Agent instance already registered: {key!r}"
            )

        self._map[key] = agent

    def remove(
        self,
        key: AgentInstanceKey,
        *,
        expected_agent: object,
    ) -> None:
        existing = self._map.get(key)

        if existing is None:
            return

        if existing is not expected_agent:
            raise RuntimeError(
                "Cannot remove another agent instance"
            )

        del self._map[key]

    def lock(self, key: AgentInstanceKey) -> asyncio.Lock:
        lock = self._locks.get(key)

        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock

        return lock

    def exists(self, key: AgentInstanceKey) -> bool:
        return key in self._map

    def keys(self) -> Iterable[AgentInstanceKey]:
        return tuple(self._map.keys())

    def values(self) -> Iterable[object]:
        return tuple(self._map.values())

    def items(
        self,
    ) -> Iterable[tuple[AgentInstanceKey, object]]:
        return tuple(self._map.items())

    def list_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
    ) -> tuple[str, ...]:
        return tuple(
            key.agent_id
            for key in self._map
            if key.tenant_id == tenant_id
            and key.user_id == user_id
        )

    def count(self) -> int:
        return len(self._map)
