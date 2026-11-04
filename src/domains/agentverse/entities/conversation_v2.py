"""Persistent communication identity, independent of sockets and runtimes."""

from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w-]+$")]
# Exact ``id@version`` activity contract key (``ActivityContract.key()``).
ActivityKey = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
        max_length=100,
    ),
]


def _absent(value: Any) -> bool:
    return value is None


ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w.@:-]+$")]


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


class ActivityStartReferences(BaseModel):
    """Human-supplied references only; coordinator-owned slots are never expressible."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    draft: ResourceId


class ActivityStartV2(BaseModel):
    """ENKI-1.0-B / COORD-0.1: an explicit human request to start (or resume) an
    activity. References are identifiers, never authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: ActivityKey
    references: ActivityStartReferences


class ActivityCloseV2(BaseModel):
    """An explicit human request to close one of the conversation's own contexts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context_id: ResourceId
    # Human-initiated closure only; ``completed`` follows a submitted proposal (COORD-0.3).
    reason: Literal["abandoned"]


class ActivityAnswerV2(BaseModel):
    """COORD-0.2: the human's answer (the event's ``message``) to one context's Manuscript,
    bound to the revision the human answered. Neither carries authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context_id: ResourceId
    expected_revision: StrictInt = Field(ge=1)


ActivityPayload = ActivityStartV2 | ActivityCloseV2 | ActivityAnswerV2
PAYLOADS: dict[str, type[BaseModel]] = {
    "activity.start": ActivityStartV2,
    "activity.close": ActivityCloseV2,
    "activity.answer": ActivityAnswerV2,
}


class ClientEventV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: Identifier
    event_id: Identifier
    type: Literal["chat.message", "activity.start", "activity.close", "activity.answer"]
    response_mode: Literal["context", "llm"] = "context"
    agent_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=4)
    # Absent fields are never serialized: a ``chat.message`` dumps (and so digests in
    # the turn journal) exactly as before activity events existed.
    message: str | None = Field(default=None, min_length=1, max_length=8192, exclude_if=_absent)
    activity: ActivityPayload | None = Field(default=None, exclude_if=_absent)

    @field_validator("agent_ids")
    @classmethod
    def distinct_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate targets")
        return value

    @model_validator(mode="after")
    def _typed_payload(self) -> Self:
        if self.type == "chat.message":
            if self.message is None or self.activity is not None:
                raise ValueError("chat.message carries exactly a message")
            return self
        # Only an answer carries the human's message; start and close never do.
        if (
            not isinstance(self.activity, PAYLOADS[self.type])
            or (self.message is not None) != (self.type == "activity.answer")
            or self.response_mode != "context"
            or len(self.agent_ids) != 1
        ):
            raise ValueError(f"{self.type} carries exactly its activity payload for one Agent")
        return self
