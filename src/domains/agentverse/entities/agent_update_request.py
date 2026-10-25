from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from src.domains.agentverse.tools.spec import AgentToolRef

# Allow: letters, numbers, spaces, hyphens, underscores.
# Must start/end with alnum; no leading/trailing - or _
NAME_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9 _-]*[A-Za-z0-9])?$"

NameStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=NAME_PATTERN,
    ),
]


class AgentUpdateRequest(BaseModel):
    """
    Request model for updating an existing agent.
    All fields are optional - only provided fields will be updated.
    """

    name: NameStr | None = Field(
        None, description="1-100 chars; letters, numbers, spaces, -, _; no leading/trailing -/_"
    )
    type: str | None = Field(
        None, description="Agent type; must resolve to a registered agent spec/class"
    )
    role_name: str | None = None
    personality_key: str | None = None
    prompt: str | None = None

    # back-ends: optional; can be updated
    llm_type: str | None = None
    image_type: str | None = None
    audio_type: str | None = None
    video_type: str | None = None
    embedding_type: str | None = None
    db_type: str | None = None
    cache_type: str | None = None
    knowledge_db_type: str | None = None
    messaging_type: str | None = None

    # Group-based visibility
    visibility: Literal["public", "private"] | None = None
    whitelisted_groups: list[str] | None = None

    tools: list[AgentToolRef] | None = None
    workflows: list[str] | None = None
    rule_pack_ids: list[str] | None = None
    collections: list[str] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_runtime_metadata(cls, value: Any) -> Any:
        if isinstance(value, dict) and {"runtime_version", "genesis_local_seed"} & value.keys():
            raise ValueError("Runtime version and seed provenance are managed by the server")
        return value

    model_config = {"extra": "allow", "populate_by_name": True}

    def model_dump_for_update(self) -> dict[str, Any]:
        """
        Return a dictionary with only the fields that were explicitly set (not None).
        This is useful for partial updates.
        """
        return {k: v for k, v in self.model_dump(exclude_unset=True).items() if v is not None}
