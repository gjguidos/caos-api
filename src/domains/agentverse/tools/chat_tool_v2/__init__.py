
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import (
    tool_registry_instance as tool_registry,
)
from src.domains.agentverse.tools.base import BaseTool, ToolConfig


class ChatToolV2Config(ToolConfig):
    """Per-agent conversational context configuration."""

    history_window: int = Field(default=12, ge=0)
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.3, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2)
    frequency_penalty: float = Field(default=0.0, ge=-2, le=2)

    lead_with_answer: bool = True
    max_words_hint: int = Field(default=250, gt=0)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class ChatContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    history: list[ChatMessage] = Field(default_factory=list)


class ChatContextResult(BaseModel):
    """Internal context contribution consumed by the agent."""

    system_overrides: list[str] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    user_overrides: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    hints: dict[str, Any] = Field(default_factory=dict)
    usage_meta: dict[str, Any] = Field(default_factory=dict)


@tool_registry.register_tool(
    key="chat_v2",
    name="Chat Context Tool V2",
    description=(
        "Provides conversational history and generation hints "
        "without performing LLM inference."
    ),
    version="2.0.0",
    metadata={
        "ownership": "system",
        "tool_class": "conversational",
        "execution_mode": "context_only",
        "llm_free": True,
        "provides_context": True,
        "requires_collection": False,
        "tags": ["builtin", "conversational", "cognitive"],
        "config_schema": (
            "src.domains.agentverse.tools.chat_tool_v2.ChatToolV2Config"
        ),
        "config": ChatToolV2Config().model_dump(),
        "outputs": [],
        "synaptic": {
            "capabilities": [
                {
                    "signal": "chat.context",
                    "access": "core",
                    "trusted_lines": [],
                },
            ],
        },
        "ui": {
            "icon": "💬",
            "color": "#2563eb",
            "category": "conversation",
        },
        "supports": {
            "streaming": False,
            "attachments": [],
            "multi_turn_memory": True,
        },
    },
)
class ChatToolV2(BaseTool):
    """
    Context-only conversational capability.

    Does not:
      - Generate answers
      - Invoke an LLM
      - Apply personality
      - Execute workflows
      - Communicate with the client

    Provides context for the calling agent's cognitive process.
    """

    key = "chat_v2"
    name = "Chat Context Tool V2"
    version = "2.0.0"

    required_dependencies: list[str] = []
    capabilities = ["chat.context"]

    parameters = {
        "text": {
            "type": "string",
            "required": True,
        },
        "history": {
            "type": "array",
            "required": False,
        },
    }

    def __init__(
        self,
        config: ChatToolV2Config | Mapping[str, Any] | None = None,
    ) -> None:
        cfg = self._normalize_config(config)

        super().__init__(config=cfg)
        self.config: ChatToolV2Config = cfg

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💬 CHAT V2] initialized",
            extra={
                "tool_key": self.key,
                "history_window": cfg.history_window,
            },
        )

    @staticmethod
    def _normalize_config(
        config: ChatToolV2Config | Mapping[str, Any] | None,
    ) -> ChatToolV2Config:
        if isinstance(config, ChatToolV2Config):
            return config

        if isinstance(config, BaseModel):
            return ChatToolV2Config.model_validate(
                config.model_dump()
            )

        if isinstance(config, Mapping):
            return ChatToolV2Config.model_validate(
                dict(config)
            )

        if config is None:
            return ChatToolV2Config()

        raise TypeError(
            f"Unsupported ChatToolV2 config: {type(config).__name__}"
        )

    async def provide_context(
        self,
        *,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        request = ChatContextRequest.model_validate(
            {
                "text": text,
                "history": history or [],
            }
        )

        history_limit = self.config.history_window * 2

        messages = (
            request.history[-history_limit:]
            if history_limit > 0
            else []
        )

        result = ChatContextResult(
            messages=messages,
            hints={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "presence_penalty": self.config.presence_penalty,
                "frequency_penalty": self.config.frequency_penalty,
                "lead_with_answer": self.config.lead_with_answer,
                "max_words_hint": self.config.max_words_hint,
            },
            usage_meta={
                "provider": self.key,
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💬 CHAT V2] context_ready",
            extra={
                "tool_key": self.key,
                "history_in": len(request.history),
                "history_used": len(messages),
            },
        )

        return result.model_dump()
