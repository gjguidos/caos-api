"""First-class Conversation Tool (CONVERSATION-0.1A, ADR-0004).

A declaration, not a second chat engine: no transport, prompt, model client, permit or
grant is constructed here. V2 turns still flow through the existing dispatcher and the
``chat.context`` / ``chat.response`` providers; the registry-driven resolver
(``agents.conversational_composition_v2``) reads this Tool's attachment and configuration.
V1 direct execution is deliberately denied.
"""

from typing import Any, ClassVar

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.conversation_tool.spec import (
    CONTEXT_PROVIDER,
    CONTEXT_SIGNAL,
    RESPONSE_PROVIDER,
    RESPONSE_SIGNAL,
    SIGNAL_VERSION,
    TOOL_KEY,
    TOOL_VERSION,
    ConversationToolConfig,
    conversation_tool,
)
from src.domains.agentverse.tools.spec import SynapticToolSpec


@tool_registry.bind_spec(conversation_tool)
class ConversationTool(BaseTool):
    key = TOOL_KEY
    name = "Conversation"
    version = TOOL_VERSION
    parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: ConversationToolConfig | dict[str, Any] | None = None) -> None:
        # BaseTool's execution settings are inert: execute/_run always deny.
        super().__init__()
        self._conversation_config = ConversationToolConfig.model_validate(
            {} if config is None else config
        )

    async def execute(self, socket_emitter: Any = None, **kwargs: Any) -> Any:
        raise EventPermissionError("Conversation runs only through V2 conversation; V1 is denied")

    async def _run(self, socket_emitter: Any = None, **params: Any) -> Any:
        raise EventPermissionError("Conversation has no direct execution path")


EXPECTED_ACTIONS = {
    (CONTEXT_SIGNAL, SIGNAL_VERSION, CONTEXT_PROVIDER),
    (RESPONSE_SIGNAL, SIGNAL_VERSION, RESPONSE_PROVIDER),
}


def validate_conversation_tool(tools: ToolRegistry = tool_registry) -> None:
    """Fail closed at trusted startup if the registered declaration drifted.

    Checks the actual registered metadata: the exact published version and exactly the
    two existing conversation actions, ``granted`` and without trusted lines. Provider
    bindings are not required here: registration and attachment grant nothing.
    """
    item = tools.get_info(TOOL_KEY)
    if item is None:
        raise ValueError("Conversation Tool registration is missing")
    if item.version != TOOL_VERSION:
        raise ValueError("Conversation Tool version mismatch")
    declared = SynapticToolSpec.model_validate(item.metadata.get("synaptic") or {})
    actions = [(a.signal, a.version, a.provider) for a in declared.capabilities]
    if len(actions) != len(EXPECTED_ACTIONS) or set(actions) != EXPECTED_ACTIONS:
        raise ValueError("Conversation Tool must declare exactly chat.context and chat.response")
    if any(a.access != "granted" or a.trusted_lines for a in declared.capabilities):
        raise ValueError("Conversation actions must require independent grants")
