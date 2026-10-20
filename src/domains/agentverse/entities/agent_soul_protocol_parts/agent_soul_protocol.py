from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol_metadata import (
    AgentSoulProfileMetadata,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.basic_profile import BasicProfile
from src.domains.agentverse.entities.agent_soul_protocol_parts.cognitive_profile import (
    CognitiveProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.culinary_profile import (
    CulinaryProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.cultural_profile import (
    CulturalProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.economic_profile import (
    EconomicProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.expression_profile import (
    ExpressionProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.identity_profile import (
    IdentityProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.personal_profile import (
    PersonalProfile,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.sensory_profile import SensoryProfile
from src.domains.agentverse.entities.agent_soul_protocol_parts.spiritual_profile import (
    SpiritualProfile,
)


# ── A single recursive diff helper ─────────────────────────────
def _deep_trim(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in raw.items():
        if k not in base:
            out[k] = v
        else:
            b = base[k]
            if isinstance(v, dict) and isinstance(b, dict):
                child = _deep_trim(v, b)
                if child:
                    out[k] = child
            elif v != b:
                out[k] = v
    return out


class AgentSoulProtocol(BaseModel):
    """
    Agent Soul Protocol (ASP) - a container of every profile section.
    Subclasses may override any field with a specialised profile instance.
    """

    def minimal_dump(self) -> dict[str, Any]:
        """Return only the fields that differ from a blank soul."""
        baseline = AgentSoulProtocol()  # canonical defaults
        raw = self.model_dump(exclude_none=True)
        base = baseline.model_dump(exclude_none=True)
        return _deep_trim(raw, base)

    agent_soul_profile_metadata: AgentSoulProfileMetadata = Field(  # ⚠️ use default_factory
        default_factory=AgentSoulProfileMetadata
    )
    basic_profile: BasicProfile = Field(default_factory=BasicProfile)

    cognitive_profile: CognitiveProfile = Field(default_factory=CognitiveProfile)
    culinary_profile: CulinaryProfile = Field(default_factory=CulinaryProfile)
    cultural_profile: CulturalProfile = Field(default_factory=CulturalProfile)
    economic_profile: EconomicProfile = Field(default_factory=EconomicProfile)
    expression_profile: ExpressionProfile = Field(default_factory=ExpressionProfile)
    identity_profile: IdentityProfile = Field(default_factory=IdentityProfile)
    personal_profile: PersonalProfile = Field(default_factory=PersonalProfile)
    sensory_profile: SensoryProfile = Field(default_factory=SensoryProfile)
    spiritual_profile: SpiritualProfile = Field(default_factory=SpiritualProfile)

    # Accept unknown top-level keys if you ever extend the schema at runtime
    model_config = ConfigDict(extra="allow")
