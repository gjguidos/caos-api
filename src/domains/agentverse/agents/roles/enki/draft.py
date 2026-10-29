"""Versioned draft shape only; never admission, validation authority or execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput


class AgentCreationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_creation_draft"]
    schema_version: Literal[1]
    resource_type: Literal["agent"]
    creation: AgentCreationInput
