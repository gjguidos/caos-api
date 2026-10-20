from __future__ import annotations

from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent
from src.domains.agentverse.agents.specs.orchestration_agent import orchestration_spec
from src.domains.agentverse.registries.registries import agent_registry_instance


@agent_registry_instance.bind_spec(orchestration_spec)
class OrchestrationAgent(ChatAgent):
    """Thin subclass that registers the 'orchestration' agent type.

    Reuses the ChatAgent runtime (LLM tool-routing, @workflow DSL). Which
    workflows it may run comes from `agent.workflows`, validated at create time
    in AgentService._validate_and_prepare_config; the workflow tool itself holds
    no workflow identity.
    """
