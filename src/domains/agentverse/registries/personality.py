from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import register_registry
from src.domains.agentverse.utils.canonicalize import canonicalize


@register_registry(name="Personality", icon="💫")
class PersonalityProfileRegistry(Registry[type[AgentSoulProtocol]]):
    """
    Specialized Registry for managing predefined EVA Personality Archetypes.

    This registry handles the classification, retrieval, and instantiation of
    personality blueprints used to imbue EVA agents with psychological depth.
    """

    name = "personality"
    description = "Registry for EVA personality archetypes"
    version = "1.0.0"

    def __init__(self):
        super().__init__(name=self.name)

    def build(self, key: str, **kwargs) -> AgentSoulProtocol:
        """
        Instantiate a personality by its canonical key.
        """
        component_class = self.get(key)

        instance = component_class(**kwargs)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[💫 REGISTRY][✨ PERSONALITY] build_success",
            extra={
                "registry_name": self._name,
                "resolved_key": canonicalize(key),
                "instance_type": type(instance).__name__,
            },
        )

        return instance

    def _validate_component(self, component: type[AgentSoulProtocol]) -> None:
        if not issubclass(component, AgentSoulProtocol):
            AGENTVERSE_LOGGER.error(
                LogDomain.NERV_HQ,
                "[💫 REGISTRY][💥 PERSONALITY] validation_failed",
                extra={
                    "registry_name": self._name,
                    "component_name": getattr(component, "__name__", repr(component)),
                    "reason": "not_subclass_of_AgentSoulProtocol",
                },
            )
            raise TypeError("Only subclasses of PersonalityProfile are allowed.")
