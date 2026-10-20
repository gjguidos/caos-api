from __future__ import annotations

from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent
from src.domains.agentverse.agents.specs.form_agent import form_agent_spec
from src.domains.agentverse.registries.registries import agent_registry_instance


@agent_registry_instance.bind_spec(form_agent_spec)
class CrawlerAgent(ChatAgent):
    """Thin subclass that registers the 'orchestration' agent type.

    Reuses the ChatAgent runtime (LLM tool-routing, @workflow DSL). The
    'orchestration' invariant — pinned workflow_key on the workflow tool — is
    enforced at create time in AgentService._validate_and_prepare_config.
    """
