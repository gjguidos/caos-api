from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
    AgentDefinitionRepository,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository


def build_agent_definition_repository(
    *,
    repository: MongoDBRepository,
) -> AgentDefinitionRepository:
    # Reuses the application's shared repository/connection pool; never creates a client.
    return AgentDefinitionRepository(repository)
