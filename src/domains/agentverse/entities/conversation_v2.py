"""Persistent communication identity, independent of sockets and runtimes."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w-]+$")]


class ConversationV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    tenant_id: Identifier
    member_user_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    agent_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    visibility: Literal["members"] = "members"


class CreateConversationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=8)


class ClientEventV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: Identifier
    event_id: Identifier
    type: Literal["chat.message"]
    response_mode: Literal["context", "llm"] = "context"
    agent_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=4)
    message: str = Field(min_length=1, max_length=8192)

    @field_validator("agent_ids")
    @classmethod
    def distinct_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate targets")
        return value
