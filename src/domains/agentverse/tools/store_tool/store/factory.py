from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

if TYPE_CHECKING:
    from src.domains.agentverse.tools.store_tool.models import ResourceStore
    from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
        AgentDefinitionRepository,
    )


def build_agent_definition_store(
    *,
    persistence: AgentDefinitionRepository,
) -> AgentDefinitionStore:
    return AgentDefinitionStore(persistence=persistence)


def build_approved_stores(
    *,
    agent_store: AgentDefinitionStore,
) -> Mapping[str, ResourceStore]:
    """The closed, server-defined resource catalog; callers can only select from it."""
    return MappingProxyType({agent_store.resource_type: agent_store})
