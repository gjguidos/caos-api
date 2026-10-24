from __future__ import annotations

from src.domains.agentverse.registries.agent_registry import AgentRegistry
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.personality import (
    PersonalityProfileRegistry,
)
from src.domains.agentverse.registries.role_registry import RoleRegistry
from src.domains.agentverse.registries.rulepack_registry import (
    RulePackRegistry,
)
from src.domains.agentverse.registries.synaptic_registry import (
    SynapticRegistry,
)
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.registries.tool_runtime_dependency_registry import (
    ToolRuntimeDependencyRegistry,
)
from src.domains.agentverse.registries.workflow_registry import (
    WorkflowRegistry,
)

agent_registry_instance = AgentRegistry()
personality_registry_instance = PersonalityProfileRegistry()
tool_registry_instance = ToolRegistry()
role_registry_instance = RoleRegistry()
rulepack_registry_instance = RulePackRegistry()
workflow_registry_instance = WorkflowRegistry()
tool_runtime_dependency_registry_instance = (
    ToolRuntimeDependencyRegistry()
)
synaptic_registry_instance = SynapticRegistry()

registries: dict[str, Registry] = {
    "agent": agent_registry_instance,
    "personality": personality_registry_instance,
    "tool": tool_registry_instance,
    "role": role_registry_instance,
    "tool_runtime_dependency": (
        tool_runtime_dependency_registry_instance
    ),
    "rulepack": rulepack_registry_instance,
    "synaptic": synaptic_registry_instance,
    "workflow": workflow_registry_instance,
}