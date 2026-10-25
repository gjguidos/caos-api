from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class SynapseState(StrEnum):
    CREATED = "created"
    ROUTED = "routed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Synapse:
    signal: str
    source: str
    target: str
    tenant_id: str
    payload: dict[str, Any]

    synapse_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class SynapticGrants:
    """Root invocation permissions, loaded only by trusted composition code."""

    invoke: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "invoke", frozenset(self.invoke))


@dataclass(frozen=True, slots=True)
class TrustedLine:
    """Explicit delegation edge; omitted scopes apply to every granted root."""

    source: str
    target: str
    tenant_id: str | None = None
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class SynapticExecutionContext:
    principal_id: str
    tenant_id: str
    active_signal: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    # Exchange-issued path, excluding the signal currently being requested.
    invocation_chain: tuple[str, ...] = ()
    permit: Any = None
    gate: Any = None
