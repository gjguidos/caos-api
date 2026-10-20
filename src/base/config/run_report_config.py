from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class RunReportConfig(BaseSettings):
    """Configuration for persisted agent run reports."""

    run_report_persistence_enabled: bool = Field(
        default=True,
        description="Persist a run report per orchestrated turn to Mongo.",
    )
    run_report_ttl_days: int = Field(
        default=30,
        description="Days a run report is kept before Mongo's TTL monitor removes it. "
        "Set to 0 to keep reports indefinitely.",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
