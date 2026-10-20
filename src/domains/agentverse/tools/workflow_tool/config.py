from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field

from src.domains.agentverse.tools.base import ToolConfig  # <-- important
from src.domains.agentverse.tools.workflow_tool.constants import (
    DEFAULT_MAX_STEPS_PER_CALL,
    DEFAULT_RUN_TTL_SECONDS,
)


class WorkflowToolConfig(ToolConfig):
    # ToolConfig likely already defines: enabled: bool = True, economy fields, etc.
    enabled: bool = True

    max_steps_per_call: int = Field(default=DEFAULT_MAX_STEPS_PER_CALL, ge=1, le=1000)
    run_ttl_seconds: int | None = Field(default=DEFAULT_RUN_TTL_SECONDS, ge=60)


def normalize_config(config: Any) -> WorkflowToolConfig:
    if config is None:
        return WorkflowToolConfig()
    if isinstance(config, WorkflowToolConfig):
        return config
    if isinstance(config, BaseModel):
        return WorkflowToolConfig.model_validate(config)
    if isinstance(config, Mapping):
        return WorkflowToolConfig(**dict(config))
    if isinstance(config, SimpleNamespace):
        return WorkflowToolConfig(**vars(config))
    raise TypeError(f"WorkflowTool config must be dict/BaseModel/namespace; got {type(config)}")
