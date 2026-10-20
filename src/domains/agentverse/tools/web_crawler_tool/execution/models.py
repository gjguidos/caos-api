from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ProcessedPagePayload(BaseModel):
    page_id: str
    run_id: str
    lease_id: str | None = None
    url_hash: str | None = None


class ProcessedPageRecord(BaseModel):
    page_id: str
    run_id: str
    url: str
    final_url: str
    status_code: int
    content_type: str
    processed_at: datetime
