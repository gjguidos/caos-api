from __future__ import annotations

from src.domains.agentverse.agents.agent_interface import AgentInterface
from src.domains.agentverse.agents.specs.agent_spec import AgentSpec
from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.mixins.spec_binding import SpecBindingMixin
from src.domains.agentverse.registries.utils.register_registry import register_registry


@register_registry(name="Agent Registry", icon="🤖")
class AgentRegistry(
    SpecBindingMixin[AgentSpec],
    Registry[type[AgentInterface]],
):
    name = "agent"
    description = "Registry for EVA agents"

    def __init__(self) -> None:
        super().__init__(name=self.name)

    def _validate_component(self, component: type) -> None:
        if not issubclass(component, AgentInterface):
            raise RegistrationError(
                message=f"Agent `{component.__name__}` must inherit from AgentInterface",
                details={"component": component.__name__},
            )