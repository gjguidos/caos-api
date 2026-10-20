import logging

from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.registries import personality_registry_instance

logger = logging.getLogger(__name__)


class PersonalityService:
    def extract_soul(self, key: str) -> AgentSoulProtocol:
        logger.info(key)
        cls = personality_registry_instance.get(key)
        return cls()
