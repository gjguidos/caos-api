from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PageParseJob(BaseModel):
    schema_name: Literal["crawler.page_parse_job.v1"] = (
        "crawler.page_parse_job.v1"
    )

    run_id: str
    page_id: str
    raw_page_key: str

    url: str
    final_url: str
    status_code: int
    content_type: str

    requested_reports: list[str] | None = None
    created_at: datetime

@dataclass(frozen=True)
class RawFetchedPage:
    page_id: str
    run_id: str
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes