from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.basic_profile import BasicProfile
from src.domains.agentverse.registries import personality_registry_instance


@personality_registry_instance.register(
    metadata={"ownership": "global"},
    name="Empty EVA", description="Base for creation of new personalities"
)
class QuickAssistant(AgentSoulProtocol):
    basic_profile: BasicProfile = BasicProfile(
        name="Empty EVA",
        description="Answers concisely and without fluff.",
        communication_style="helpful",
    )
