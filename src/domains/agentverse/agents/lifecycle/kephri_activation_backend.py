from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
    AgentInstanceRegistryV2,
)


class KhepriActivationBackend:
    def __init__(
        self,
        *,
        registry: AgentInstanceRegistryV2,
    ) -> None:
        self._registry = registry

    def get_instance(
        self,
        key: AgentInstanceKey,
    ) -> object | None:
        return self._registry.get(key)

    def register_instance(
        self,
        key: AgentInstanceKey,
        agent: object,
    ) -> None:
        self._registry.register(key, agent)