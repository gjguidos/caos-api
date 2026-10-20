"""The persisted form of a run report.

One document per orchestrated turn, keyed on the ``run_id`` the orchestrator
mints at loop start. The report body itself is stored opaquely under
``report``: it is a v2 document built by
``src/domains/agentverse/agents/orchestrator/report.py`` and validating its
inner shape here would only mean maintaining the schema twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRunReport(BaseModel):
    id: str | None = Field(default=None, alias="_id")
    run_id: str
    session_id: str | None = None
    agent_id: str | None = None
    created_by: str | None = None  # user who owns this run, for read filtering
    report: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None  # the TTL index deletes the doc at this time

    class Config:
        populate_by_name = True
