from __future__ import annotations

from typing import Any

from dependency_injector import containers, providers

from src.domains.agentverse.tools.store_tool import StoreTool
from src.domains.agentverse.tools.store_tool.persistence.factory import (
    build_agent_definition_repository,
)
from src.domains.agentverse.tools.store_tool.store.factory import (
    build_agent_definition_store,
    build_approved_stores,
)


class StoreToolContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for the Store tool.
    """

    # ------------------------------------------------------------------
    # External dependencies
    # ------------------------------------------------------------------

    mongodb_repository: providers.Dependency[Any] = providers.Dependency()
    utu_authority: providers.Dependency[Any] = providers.Dependency()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    agent_definition_repository = providers.Singleton(
        build_agent_definition_repository,
        repository=mongodb_repository,
    )

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------

    agent_definition_store = providers.Singleton(
        build_agent_definition_store,
        persistence=agent_definition_repository,
    )

    approved_stores = providers.Singleton(
        build_approved_stores,
        agent_store=agent_definition_store,
    )

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    tool = providers.Singleton(
        StoreTool,
        stores=approved_stores,
        authority=utu_authority,
    )
