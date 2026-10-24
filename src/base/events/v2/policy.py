from __future__ import annotations

from dataclasses import dataclass

from .contracts import EventPermissionError


@dataclass(frozen=True)
class EventGrants:
    publish: frozenset[str] = frozenset()
    subscribe: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EventPrincipal:
    agent_id: str
    tenant_id: str


class CognitiveEventPolicy:
    """Default deny. Load grants from trusted bootstrap/DB, never from an LLM."""

    def __init__(self, grants: dict[str, EventGrants]) -> None:
        self._grants = dict(grants)

    def require_publish(self, principal: EventPrincipal, event_type: str) -> None:
        if event_type not in self._grants.get(principal.agent_id, EventGrants()).publish:
            raise EventPermissionError(f"Publish denied: {principal.agent_id} -> {event_type}")

    def require_subscribe(self, agent_id: str, event_type: str) -> None:
        if event_type not in self._grants.get(agent_id, EventGrants()).subscribe:
            raise EventPermissionError(f"Subscribe denied: {agent_id} <- {event_type}")
