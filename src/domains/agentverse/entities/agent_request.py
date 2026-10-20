from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from src.domains.agentverse.agents.roles.role_spec import RoleBinding
from src.domains.agentverse.personalities.utils.default_personality import (
    DEFAULT_PERSONALITY_KEY,
)
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

RESERVED_NAMES = {
    "admin",
    "system",
    "root",
    "api",
    "null",
    "undefined",
    "test",
    "demo",
    "example",
    "default",
    "config",
    "settings",
}


class AgentRequest(BaseModel):
    user_id: str
    name: NameStr = Field(
        ..., description="1-100 chars; letters, numbers, spaces, -, _; no leading/trailing -/_"
    )
    type: str

    # role wiring
    role_name: str | None = None
    role: RoleBinding | None = Field(
        default=None,
        description="Optional structured role binding (includes rule_packs and config)",
    )

    # personality + prompt
    personality_key: str = Field(
        default=DEFAULT_PERSONALITY_KEY,
        description="Canonical registry key of the personality this agent was built from",
    )
    prompt: str

    # back-ends: optional; resolved later
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
    visibility: Literal["public", "private"] = "private"
    whitelisted_groups: list[str] = Field(
        default_factory=list,
        description="Group names that may access this agent, e.g. ['team-alpha', 'team-beta']",
    )

    tools: list[AgentToolRef] = Field(default_factory=list)

    workflows: list[str] = Field(
        default_factory=list,
        description="Workflow catalog keys this agent may run.",
    )

    # 🔽 NEW: explicit rule-pack ids (mirrors Agent.rule_pack_ids)
    rule_pack_ids: list[str] = Field(
        default_factory=list,
        description="Explicit list of rule pack IDs to attach to this agent",
    )

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("personality_key", mode="before")
    @classmethod
    def _default_personality_key(cls, v: str | None) -> str:
        if not v:
            return DEFAULT_PERSONALITY_KEY
        return v

    @field_validator("name")
    @classmethod
    def collapse_spaces_and_reserved(cls, v: str) -> str:
        # collapse multiple spaces
        v = re.sub(r"\s+", " ", v)
        if v.lower() in RESERVED_NAMES:
            raise ValueError(f"'{v}' is a reserved system name")
        return v

    @model_validator(mode="after")
    def sync_rule_packs(cls, values: AgentRequest) -> AgentRequest:
        """
        If a RoleBinding with rule_packs is provided but rule_pack_ids is empty,
        mirror them into rule_pack_ids so the agent is created with the correct pack IDs.
        """
        if values.role and values.role.rule_packs and not values.rule_pack_ids:
            values.rule_pack_ids = list(values.role.rule_packs)
        return values
