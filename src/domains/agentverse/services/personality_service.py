import logging

from src.base.entities.user import User
from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.registries import personality_registry_instance
from src.domains.agentverse.services.registry_service import RegistryService

logger = logging.getLogger(__name__)


class ClientSoul(AgentSoulProtocol):
    """Declared profile sections only; security/activation metadata stays trusted."""

    def minimal_dump(self) -> dict:
        result = super().minimal_dump()
        result.pop("agent_soul_profile_metadata", None)
        return result



class PersonalityService:
    def extract_soul_for_client(self, key: str, user: User | None) -> AgentSoulProtocol:
        from src.domains.agentverse.registries.registries import registries

        catalog = RegistryService(registries)
        item = catalog.get_info_for_client("personality", key, user)
        soul = self.extract_soul(key)
        if not catalog.client_metadata_allowed(
            soul.minimal_dump(),
            user,
            _ownership_context={
                field: item.metadata[field]
                for field in ("ownership", "tenant_id", "tenant_scoped")
                if field in item.metadata
            },
        ):
            raise KeyError("Personality unavailable")
        raw = soul.model_dump(mode="json")
        public = {field: raw[field] for field in AgentSoulProtocol.model_fields
                  if field != "agent_soul_profile_metadata" and field in raw}
        # Free-form maps are extension containers, not reviewed profile fields.
        for section, field in (("sensory_profile", "sensory_profile_notes"),
                               ("cultural_profile", "language_proficiency")):
            if isinstance(public.get(section), dict):
                public[section].pop(field, None)
        # Parsing into the canonical profile types drops subclass/extension fields.
        return ClientSoul.model_validate(public)

    def extract_soul(self, key: str) -> AgentSoulProtocol:
        logger.info(key)
        cls = personality_registry_instance.get(key)
        return cls()
