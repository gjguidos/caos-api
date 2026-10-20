from __future__ import annotations

from typing import Any, Protocol, TypedDict

from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)


class ChatMessage(TypedDict, total=False):
    role: str
    content: str
    # Optional OpenAI/Responses field identifying the tool/function that produced
    # the message; carried on tool-role history entries and passed through to the
    # backend. Bookkeeping-only keys (e.g. ``meta``) are deliberately *not* part of
    # this contract and are stripped before messages reach the model.
    name: str


class AgentLike(Protocol):
    llm: Any

    async def llm_chat_async(self, *, messages: list[ChatMessage], **params) -> Any: ...
    def build_system_prompt_from_soul(self, soul: AgentSoulProtocol) -> ChatMessage: ...


class ToolLike(Protocol):
    key: str

    async def provide_context(
        self,
        *,
        text: str,
        session_id: str | None,
        policy: dict[str, Any] | None,
        cli_flags: dict[str, Any],
        args: dict[str, Any],
    ) -> Any: ...
    def post_spec(self, *args, **kwargs) -> dict[str, Any] | None: ...


class ToolManagerLike(Protocol):
    def can_run(self, key: str) -> bool: ...
    def ensure_instance(self, key: str) -> ToolLike: ...

    _factories: dict[str, dict[str, Any]]
