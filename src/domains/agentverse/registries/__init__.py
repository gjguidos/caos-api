"""
AgentVerse Registry Module

This module provides a registry system for managing and accessing different components
in the AgentVerse ecosystem. It includes base registry functionality, specialized registries
for different component types, and a centralized registry management system.

Key Components:
    - Base Registry: Generic registry implementation for component management
    - Agent Registry: Specialized registry for agent components
    - Component Registries: Pre-configured registries for memories, LLMs, parsers, etc.
    - Registry Management: Utilities for accessing and managing registries

Available Registries:
    - agent_registry: Registry for agent components

Example Usage:
    >>> from src.core.agentverse.registry import agent_registry, Registry
    >>>
    >>> # Register a component
    >>> @agent_registry.register("my_agent")
    >>> class MyAgent(BaseAgent):
    ...     pass
    >>>
    >>> # Get a registry
    >>> from src.core.agentverse.registry import get_registry
    >>> parser_reg = get_registry("parser")
"""

from src.domains.agentverse.registries.registries import (
    agent_registry_instance,
    personality_registry_instance,
    role_registry_instance,
    rulepack_registry_instance,
    tool_registry_instance,
    tool_runtime_dependency_registry_instance,
    workflow_registry_instance,
)
from src.domains.agentverse.registries.utils.utils import get_registry

__all__ = [
    "agent_registry_instance",
    "get_registry",
    "personality_registry_instance",
    "tool_registry_instance",
    "tool_runtime_dependency_registry_instance",     
    "role_registry_instance",
    "rulepack_registry_instance",
    "workflow_registry_instance",
]
