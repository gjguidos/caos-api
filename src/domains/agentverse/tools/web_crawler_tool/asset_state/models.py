from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AssetState(BaseModel):
    asset_key: str
    url: str

    asset_type: Literal["media", "document"]
    subtype: str | None = None

    first_seen_at: datetime
    last_seen_at: datetime

    source_pages_count: int = Field(
        default=0,
        ge=0,
    )

    occurrence_count: int = Field(
        default=0,
        ge=0,
    )

    is_internal: bool = False

    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )


class AssetObservation(BaseModel):
    """
    One unique observation of an asset from one source page
    during one crawl run.
    """

    observation_key: str
    run_id: str
    asset_key: str
    source_page_url: str
    observed_at: datetime