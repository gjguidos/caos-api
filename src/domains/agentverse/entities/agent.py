from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.domains.agentverse.tools.spec import AgentToolRef


class Agent(BaseModel):
    exposure: Literal["public", "internal"] = "public"
    ownership: Literal["system", "tenant", "global"] | None = None
    tenant_id: str | None = None

    # Server-owned execution contract. Historical definitions use the V1 runtime.
    runtime_version: Literal["v1", "v2"] = "v1"
    id: str
    creator: str | None = None  # Creator username or session key
    name: str
    system_name: str
    type: str
    role_name: str | None = None
    personality_key: str | None = None
    prompt: str
    chat_url: str

    llm_type: str
    audio_type: str
    image_type: str | None = None
    video_type: str | None = None
    db_type: str | None
    cache_type: str | None
    knowledge_db_type: str | None
    embedding_type: str | None = None
    commbridge: Any | None = None  # Communication bridge for real-time messaging
    collections: list[str] | None = Field(default_factory=list)
    messaging_type: str | None

    dna_sequence: str
    tools: list[AgentToolRef] = Field(default_factory=list)

    workflows: list[str] = Field(
        default_factory=list,
        description="Workflow catalog keys this agent may run.",
    )

    rule_pack_ids: list[str] = Field(default_factory=list)

    # Group-based visibility (security layer)
    visibility: Literal["public", "private"] = "public"
    whitelisted_groups: list[str] = Field(
        default_factory=list,
        description="Group names that may access this agent, e.g. ['team-alpha', 'team-beta']",
    )
