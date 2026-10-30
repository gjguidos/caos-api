"""Built-in, published Manuscripts. Registered by trusted bootstrap only."""

from __future__ import annotations

from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry

BUILTIN = (AGENT_CREATION_INTERVIEW,)


def register_builtin(registry: ManuscriptRegistry) -> None:
    """Idempotent; a changed published definition fails closed (fingerprint pin)."""
    for definition in BUILTIN:
        registry.register_definition(definition)
