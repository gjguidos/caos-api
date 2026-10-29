from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from dependency_injector import containers, providers

from src.domains.agentverse.tools.validation_tool import DataValidationTool
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator
from src.domains.agentverse.tools.validation_tool.models import ResourceValidator


def build_validators(
    *, agent_validator: AgentDefinitionValidator
) -> Mapping[str, ResourceValidator]:
    """The closed, server-defined resource catalog; callers can only select from it."""
    return MappingProxyType({agent_validator.resource_type: agent_validator})


class ValidationToolContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for the Data Validation tool.
    """

    # ------------------------------------------------------------------
    # External dependencies
    # ------------------------------------------------------------------

    registries: providers.Dependency[Any] = providers.Dependency()
    workflow_service: providers.Dependency[Any] = providers.Dependency()
    utu_authority: providers.Dependency[Any] = providers.Dependency()

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    agent_validator = providers.Singleton(
        AgentDefinitionValidator,
        registries=registries,
        workflow_service=workflow_service,
    )

    validators = providers.Singleton(
        build_validators,
        agent_validator=agent_validator,
    )

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    tool = providers.Singleton(
        DataValidationTool,
        validators=validators,
        authority=utu_authority,
    )
