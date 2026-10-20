from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SitemapCrawlStreamContext:
    run_id: str
    queue_name: str
    consumer_group: str
    consumer_name: str
    lease_seconds: int = 300
    lease_renew_interval_seconds: int = 60