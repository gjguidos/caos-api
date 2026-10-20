from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig

REFINE_SYSTEM_PROMPT = """You are a precise revision assistant.

Your task is to improve a draft without changing its intended purpose.

Rules:
- Preserve the original intent.
- Improve clarity, usefulness, and fit to audience.
- Respect the provided brief and constraints.
- Keep persona/style only when it supports the task.
- Do not add meta commentary.
- Return only the improved final text.
"""


class RefineConfig(ToolConfig):
    temperature: float = 0.2
    max_tokens: int = 1200
    default_mode: str = "semantic"
    enable_cleanup: bool = True


@tool_registry.register_tool(
    key="refine",
    name="Refine Tool",
    description="Improves a draft against the original request and brief.",
    version="1.0.0",
    metadata={
        "requires_collection": False,
        "execution_mode": "llm_only",
        "llm_free": False,
        "route": "llm_only",
        "actions": [],
        "tool_class": "refinement",
        "tags": ["builtin", "system"],
        "outputs": [
            {
                "key": "refined_text",
                "label": "Refined text",
                "type": "string",
                "path": "refined_text",
            },
            {"key": "changed", "label": "Changed", "type": "boolean", "path": "changed"},
        ],
    },
)
class RefineTool(BaseTool):
    key = "refine"
    name = "Refine Tool"
    version = "1.0.0"

    parameters = {
        "draft": {"type": "string", "required": True},
        "original_request": {"type": "string", "required": True},
        "brief": {"type": "object", "required": False},
        "policy": {"type": "object", "required": False},
        "allow_llm": {"type": "boolean", "required": False, "default": True},
        "agent": {"type": "object", "required": False},
    }

    def __init__(self, config: Any = None):
        super().__init__(config=config)
        self.config = config if isinstance(config, RefineConfig) else RefineConfig(**(config or {}))

    def _build_prompt(
        self,
        *,
        draft: str,
        original_request: str,
        brief: dict[str, Any] | None,
    ) -> str:
        brief = brief or {}
        intent = brief.get("intent") or ""
        audience = ", ".join(brief.get("audience") or [])
        tone = ", ".join(brief.get("tone") or [])
        constraints = ", ".join(brief.get("constraints") or [])
        acceptance = brief.get("acceptance") or {}

        return f"""Revise the following draft.

ORIGINAL REQUEST:
{original_request}

BRIEF:
- intent: {intent}
- audience: {audience or "unspecified"}
- tone: {tone or "unspecified"}
- constraints: {constraints or "none"}
- acceptance: {acceptance}

CURRENT DRAFT:
\"\"\"
{draft}
\"\"\"

Return only the improved final version.
"""

    async def _run(
        self,
        *,
        draft: str = "",
        original_request: str = "",
        brief: dict[str, Any] | None = None,
        allow_llm: bool = True,
        policy: Any = None,
        agent: Any = None,
        **kwargs,
    ) -> dict[str, Any]:
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][📝 REFINE] run_start",
            extra={
                "tool_key": self.key,
                "draft_len": len(draft or ""),
                "request_len": len(original_request or ""),
                "allow_llm": allow_llm,
                "has_brief": bool(brief),
            },
        )

        if not draft or not draft.strip():
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][🫧 REFINE] skipped_empty_draft",
                extra={"tool_key": self.key},
            )
            return {
                "success": True,
                "result": {
                    "refined_text": "",
                    "changed": False,
                    "reasons": ["empty draft"],
                    "quality_signals": {"preserved_intent": True, "length_delta": 0},
                },
            }

        if not allow_llm:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][🫧 REFINE] skipped_llm_disabled",
                extra={"tool_key": self.key},
            )
            return {
                "success": True,
                "result": {
                    "refined_text": draft,
                    "changed": False,
                    "reasons": ["llm disabled"],
                    "quality_signals": {"preserved_intent": True, "length_delta": 0},
                },
            }

        if agent is None or getattr(agent, "llm_chat_async", None) is None:
            raise RuntimeError("RefineTool requires agent.llm_chat_async")

        prompt = self._build_prompt(
            draft=draft,
            original_request=original_request,
            brief=brief,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🤖 REFINE] llm_refine_start",
            extra={
                "tool_key": self.key,
                "prompt_len": len(prompt),
                "temperature": float(self.config.temperature),
            },
        )

        revised = await agent.llm_chat_async(
            messages=[
                {"role": "system", "content": REFINE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=float(self.config.temperature),
        )

        revised = (revised or "").strip()
        changed = bool(revised and revised != draft)
        final_text = revised if revised else draft

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][✅ REFINE] run_complete",
            extra={
                "tool_key": self.key,
                "changed": changed,
                "input_len": len(draft),
                "output_len": len(final_text),
                "length_delta": len(final_text) - len(draft),
            },
        )

        return {
            "success": True,
            "result": {
                "refined_text": final_text,
                "changed": changed,
                "reasons": ["semantic refinement"],
                "quality_signals": {
                    "preserved_intent": True,
                    "length_delta": len(final_text) - len(draft),
                },
            },
        }
