from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig


class ChatToolConfig(ToolConfig):
    max_tokens: int = 512
    temperature: float = 0.3
    top_p: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0

    history_window: int = 12
    budget_tokens: int = 4000

    lead_with_answer: bool = True
    max_words_hint: int = 250

    economy_enabled: bool = False
    economy_currency: str = "USD"
    price_cents_per_call: int = 0


@tool_registry.register_tool(
    key="chat",
    name="Chat Synthesis Tool",
    description="General LLM response tool with persona & policy awareness.",
    version="1.0.0",
    metadata={
        "execution_mode": "context_only",
        "llm_free": False,
        "route": "invoker",
        "actions": [
            "converse.dialogue",
            "converse.qa",
            "converse.persona",
            "converse.fallback",
        ],
        "tool_class": "conversational",
        "tags": ["builtin", "conversational"],
        "requires_collection": False,
        "provides_context": True,
        "config_schema": "src.domains.agentverse.tools.chat_tool.ChatToolConfig",
        "config": ChatToolConfig().model_dump(),
        "synaptic": {
            "capabilities": [
                {
                    "signal": "chat.context",
                    "access": "core",
                    "trusted_lines": [],
                },
            ],
        },
        "outputs": [
            {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
        ],
        "ui": {
            "icon": "💬",
            "color": "#2563eb",
            "category": "conversation",
            "display_hints": ["answer-first", "concise"],
            "shortcuts": [
                {"label": "Concise", "overrides": {"max_tokens": 128}},
                {"label": "Creative", "overrides": {"temperature": 0.9}},
                {"label": "Bullet list", "overrides": {"stop": None}},
            ],
        },
        "supports": {"streaming": True, "attachments": [], "multi_turn_memory": True},
    },
)
class ChatTool(BaseTool):
    """
    Manual-activation LLM synthesis tool. Compatible envelope with RAG tool:
    returns {success, result: {answer, addons: {usage}}}.
    """

    key = "chat"
    name = "Chat Synthesis Tool"
    version = "1.0.0"

    required_dependencies = ["llm"]
    capabilities = ["general_chat", "llm_response"]

    parameters = {
        "prompt": {"type": "string", "description": "User request to answer", "required": True},
        "system": {"type": "string", "description": "Override system prompt", "required": False},
        "few_shots": {
            "type": "array",
            "description": "Few-shot examples [{'role','content'}]",
            "required": False,
        },
        "stop": {"type": "array", "description": "Stop sequences", "required": False},
        "model": {"type": "string", "description": "Model/deployment to use", "required": False},
        "temperature": {"type": "number", "description": "Sampling temperature", "required": False},
        "top_p": {"type": "number", "description": "Top-p sampling", "required": False},
        "presence_penalty": {
            "type": "number",
            "description": "Presence penalty",
            "required": False,
        },
        "frequency_penalty": {
            "type": "number",
            "description": "Frequency penalty",
            "required": False,
        },
        "max_tokens": {
            "type": "integer",
            "description": "Max completion tokens",
            "required": False,
        },
        "history": {
            "type": "array",
            "description": "Chat history [{'role','content'}]",
            "required": False,
        },
        "persona": {
            "type": "object",
            "description": "Persona dict {voice,formality,style_guides,taboo}",
            "required": False,
        },
        "policy": {
            "type": "object",
            "description": "Optional role policy to bias prompts",
            "required": False,
        },
        "session_id": {
            "type": "string",
            "description": "Per-tool memory thread id",
            "required": False,
        },
        "request": {
            "type": "object",
            "description": "FastAPI request (for dependency resolution)",
            "required": False,
        },
    }

    def __init__(self, llm=None, config: Any = None):
        cfg = self._normalize_config(config)
        super().__init__(config=cfg, llm=llm)
        self.llm = llm
        self.config: ChatToolConfig = cfg
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💬 CHAT] initialized",
            extra={
                "tool_key": self.key,
                "history_window": self.config.history_window,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )

    @staticmethod
    def _normalize_config(config: Any) -> ChatToolConfig:
        if isinstance(config, ChatToolConfig):
            return config
        if isinstance(config, BaseModel):
            return ChatToolConfig.model_validate(config)
        if isinstance(config, Mapping):
            return ChatToolConfig(**dict(config))
        if isinstance(config, SimpleNamespace):
            return ChatToolConfig(**vars(config))
        if isinstance(config, dict) or config is None:
            return ChatToolConfig(**(config or {}))
        raise TypeError(f"ChatTool config must be dict/BaseModel/namespace; got {type(config)}")

    async def provide_context(
        self,
        *,
        text: str,
        session_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        persona: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        request: Any | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        messages = []
        if history:
            messages = history[-(self.config.history_window * 2) :]

        hints = {
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "presence_penalty": self.config.presence_penalty,
            "frequency_penalty": self.config.frequency_penalty,
            "lead_with_answer": self.config.lead_with_answer,
            "max_words_hint": self.config.max_words_hint,
        }

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💬 CHAT] provide_context",
            extra={
                "tool_key": self.key,
                "text_len": len(text or ""),
                "history_in": len(history or []),
                "history_used": len(messages),
                "has_persona": bool(persona),
                "has_policy": bool(policy),
            },
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💬 CHAT] context_hints_ready",
            extra={
                "tool_key": self.key,
                "hint_keys": list(hints.keys()),
                "usage_provider": "chat_tool",
            },
        )

        return {
            "system_overrides": [],
            "messages": messages,
            "user_overrides": None,
            "attachments": [],
            "evidence": [],
            "hints": hints,
            "usage_meta": {"provider": "chat_tool"},
        }
