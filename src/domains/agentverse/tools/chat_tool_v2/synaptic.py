
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries import synaptic_registry_instance
from src.domains.agentverse.tools.chat_tool_v2 import ChatToolV2

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient


@synaptic_registry_instance.subscribe(
    "chat.context",
    provider="tool.chat",
)
async def chat_context(
    synapse: Synapse,
    client: AuthorizedSynapticClient,
) -> dict[str, Any]:
    payload = synapse.payload

    # Initial standalone integration. The next step is to
    # resolve the tool through the existing per-agent runtime.
    tool = ChatToolV2()

    return await tool.provide_context(
        text=payload["text"],
        history=payload.get("history"),
    )
