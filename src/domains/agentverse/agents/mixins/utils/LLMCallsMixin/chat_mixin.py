from __future__ import annotations

import logging

from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.base import log_outbound
from src.domains.agentverse.llm.helpers.protocols import ChatMessage

logger = logging.getLogger("llm_chat_mixin")


def _sanitize_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """
    Reduce each message to the fields the LLM backend accepts.

    History entries may carry bookkeeping keys (e.g. ``meta`` holding the raw
    @tool command for audit) that must never be sent to the model. Project each
    message onto the ChatMessage contract - ``role``/``content`` plus ``name``
    when present (tool/function messages) - and drop everything else.
    """
    clean: list[ChatMessage] = []
    for m in messages or []:
        if not isinstance(m, dict):
            clean.append(m)
            continue
        entry: ChatMessage = {"role": m.get("role", ""), "content": m.get("content", "")}
        if name := m.get("name"):
            entry["name"] = name
        clean.append(entry)
    return clean


class ChatLLMMixin:
    async def llm_chat_async(self, *, messages: list[ChatMessage], **opts) -> str:
        llm = getattr(self, "llm", None)
        if llm is None:
            raise AttributeError("No llm backend bound on agent.llm")

        fn = getattr(llm, "get_response_async", None)
        if fn is None:
            raise TypeError(
                f"{llm.__class__.__name__} must implement async def get_response_async(...)"
            )

        temperature = float(opts.get("temperature", 0.7))

        messages = _sanitize_messages(messages)
        log_outbound(messages)
        return await fn(input=messages, temperature=temperature)
