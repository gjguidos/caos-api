from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.base import require
from src.domains.agentverse.llm.helpers.protocols import ChatMessage


class VisionLLMMixin:
    async def llm_vision_chat_async(
        self,
        *,
        user_text: str = "",
        image_urls: list[str] | None = None,
        image_parts: list[dict[str, str | dict[str, str]]] | None = None,
        system: str | None = None,
        **opts,
    ) -> str:
        require(self, "vision_in")

        parts: list[dict[str, Any]] = []
        if user_text:
            parts.append({"type": "text", "text": str(user_text)})
        for u in image_urls or []:
            parts.append({"type": "image_url", "image_url": {"url": str(u)}})
        if image_parts:
            parts.extend(image_parts)

        messages: list[ChatMessage] = []
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": parts})

        # delegate to chat method on the same class (provided by composition)
        return await self.llm_chat_async(messages=messages, **opts)
