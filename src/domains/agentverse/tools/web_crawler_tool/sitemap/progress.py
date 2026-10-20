from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Literal


SitemapDiscoveryStage = Literal[
    "resolving_root",
    "discovering_sitemaps",
    "resolving_coverage",
    "building_crawl_plan",
]


@dataclass(frozen=True)
class SitemapDiscoveryProgress:
    stage: SitemapDiscoveryStage

    processed_sitemaps: int = 0
    discovered_sitemaps: int = 0
    resolved_sitemaps: int = 0
    failed_sitemaps: int = 0

    queued_sitemaps: int = 0
    active_sitemaps: int = 0

    discovered_page_urls: int = 0

    current_url: str | None = None



SitemapProgressCallback = Callable[
    [SitemapDiscoveryProgress],
    Awaitable[None],
]