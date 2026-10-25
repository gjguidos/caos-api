"""Canonical attributed transcript; neither a turn journal nor agent memory."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domains.agentverse.entities.conversation_v2 import Identifier


class ConversationMessageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    sequence: int = Field(default=0, ge=0)
    tenant_id: Identifier
    conversation_id: Identifier
    event_id: Identifier
    turn_user_id: Identifier
    participant_id: Identifier
    kind: Literal["user_message", "agent_response", "capability_contribution", "internal_event"]
    content: str = Field(max_length=65536)
    capability: str | None = None
    # Empty user audience means all CURRENT conversation members, never public.
    visible_user_ids: tuple[Identifier, ...] = ()
    visible_agent_ids: tuple[Identifier, ...] = ()
    outcome: str = "completed"

    @model_validator(mode="after")
    def provenance(self) -> "ConversationMessageV2":
        if (self.kind == "capability_contribution") != (self.capability is not None):
            raise ValueError("Only capability contributions require a capability address")
        return self


class HistoryPageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ConversationMessageV2, ...]
    next_cursor: str | None = None


class AgentHistoryViewV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Identifier
    messages: tuple[ConversationMessageV2, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def bounded_visible(self) -> "AgentHistoryViewV2":
        if any(self.agent_id not in m.visible_agent_ids for m in self.messages):
            raise ValueError("History contains messages outside agent audience")
        sequences = [m.sequence for m in self.messages]
        if sequences != sorted(set(sequences)) or any(n < 1 for n in sequences):
            raise ValueError("History must contain unique committed ordered messages")
        if sum(len(m.content) for m in self.messages) > 32768:
            raise ValueError("History exceeds character budget")
        return self

    def chat_messages(self) -> list[dict[str, str]]:
        # Deterministic contributions and journal events are never assistant answers.
        return [
            {
                "role": "user" if m.kind == "user_message" else "assistant",
                "content": (
                    f"[agent:{m.participant_id}] {m.content}"
                    if m.kind == "agent_response" and m.participant_id != self.agent_id
                    else m.content
                ),
            }
            for m in self.messages
            if m.kind in ("user_message", "agent_response")
        ]
