from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.domains.agentverse.tools.web_crawler_tool.site_state.types import (
    SiteStateSchemaVersion,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlGraphReport,
)


class PageState(BaseModel):
    url: str

    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    acquired_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    depth: int = 0

    status: str = "visited"

    acquisition_fingerprint: str | None = None

    snapshot_ref: str | None = None

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class CoverageObservation(BaseModel):
    root_url: str

    max_depth: int

    max_pages: int

    completed: bool = False

    truncated: bool = False

    acquired_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


class SiteState(BaseModel):
    schema_version: SiteStateSchemaVersion = "crawler.site_state.v2"

    domain: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    pages: dict[str, PageState] = Field(
        default_factory=dict
    )

    coverage_observations: list[
        CoverageObservation
    ] = Field(
        default_factory=list
    )

    crawl_graph: CrawlGraphReport | None = None
    crawl_graph_ref: str | None = None


class CoverageRequest(BaseModel):
    root_url: str

    max_depth: int

    freshness_ttl_seconds: int = 3600

class CoverageResult(BaseModel):
    complete: bool = False

    discovered_url_count: int = 0

    reusable_url_count: int = 0

    stale_url_count: int = 0

    missing_url_count: int = 0

    frontier_url_count: int = 0

    stale_candidates: list[str] = Field(
        default_factory=list
    )

    missing_candidates: list[str] = Field(
        default_factory=list
    )

    frontier_candidates: list[str] = Field(
        default_factory=list
    )

    reasons: list[str] = Field(
        default_factory=list
    )


class CrawlTarget(BaseModel):
    url: str

    source: Literal[
        "discovery",
        "sitemap",
        "coverage_stale",
        "coverage_missing",
        "coverage_frontier",
        "manual",
        "link",
    ]

    depth: int = 0

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    lease_id: str | None = None