from __future__ import annotations

from typing import Any

from src.domains.agentverse.llm.helpers.protocols import ChatMessage
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


def preview(text: str, n: int = 800) -> str:
    s = str(text or "")
    return s[:n] + ("…" if len(s) > n else "")


def log_outbound(messages: list[ChatMessage]) -> None:
    try:
        if not messages:
            return

        last = messages[-1]
        role = last.get("role", "")
        content = last.get("content", "")

        AGENTVERSE_LOGGER.debug(
            LogDomain.COGNITION_BAY,
            "[🧠 LLM][📤 OUTBOUND] last_message",
            extra={
                "message_role": role,
                "message_len": len(content),
                "message_preview": preview(content),
                "message_count": len(messages),
            },
        )

        for msg in reversed(messages):
            if msg.get("role", "").lower() == "system":
                sys_content = msg.get("content", "")
                AGENTVERSE_LOGGER.debug(
                    LogDomain.COGNITION_BAY,
                    "[🧠 LLM][🧾 SYSTEM] system_prompt",
                    extra={
                        "system_len": len(sys_content),
                        "system_preview": preview(sys_content),
                    },
                )
                break

    except Exception as e:
        AGENTVERSE_LOGGER.exception(
            LogDomain.COGNITION_BAY,
            "[🧠 LLM][💥 ERROR] outbound_preview_failed",
            extra={
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )


def require(agent: Any, cap: str) -> None:
    if not getattr(agent, "supports", None) or not agent.supports(cap):
        raise RuntimeError(f"Agent '{getattr(agent, 'name', '?')}' lacks capability: {cap}")
