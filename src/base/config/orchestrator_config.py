from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class OrchestratorConfig(BaseSettings):
    """Configuration for the universal orchestration loop."""

    orchestrator_quality_phases_enabled: bool = Field(
        default=False,
        description="Run the analyze LLM brief-shaping, refine and evaluate phases. "
        "Adds up to 3 LLM round-trips per turn on top of the task phase.",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
