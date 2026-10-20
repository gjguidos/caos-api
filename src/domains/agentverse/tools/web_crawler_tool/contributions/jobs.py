from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.base.queues.models import AgentVerseJob
from src.domains.agentverse.tools.web_crawler_tool.contributions.models import (
    REPORT_CONTRIBUTION_SCHEMA,
    ReportContributionPayload,
)


def build_report_contribution_job(
    *,
    payload: ReportContributionPayload,
) -> AgentVerseJob:
    return AgentVerseJob(
        schema_name=REPORT_CONTRIBUTION_SCHEMA,
        job_id=str(uuid4()),
        run_id=payload.run_id,
        chunk_id=None,
        payload=payload.model_dump(
            mode="json"
        ),
        created_at=datetime.now(UTC),
    )