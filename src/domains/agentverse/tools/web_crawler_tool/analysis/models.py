from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PageAnalysis(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str

    media_items: list[dict[str, Any]] = Field(
        default_factory=list
    )

    document_items: list[dict[str, Any]] = Field(
        default_factory=list
    )