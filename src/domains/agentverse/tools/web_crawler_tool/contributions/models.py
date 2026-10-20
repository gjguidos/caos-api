from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

REPORT_CONTRIBUTION_SCHEMA = (
    "crawler.report_contribution_job.v1"
)


class ReportContributionPayload(BaseModel):
    run_id: str
    page_id: str
    source_page_url: str

    url_hash: str | None = None
    lease_id: str | None = None

    increments: dict[str, int] = Field(
        default_factory=dict
    )

    add_to_sets: dict[str, list[Any]] = Field(
        default_factory=dict
    )

    media_items: list[dict[str, Any]] = Field(
        default_factory=list
    )