from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentVerseJob(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
    )

    schema_name: str = Field(
        alias="schema",
    )
    job_id: str
    run_id: str | None = None
    chunk_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    attempt: int = Field(default=0, ge=0)