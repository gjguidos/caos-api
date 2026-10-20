from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.snapshots.store import (
    CrawlSnapshotStore,
)


def build_crawl_snapshot_store(
    *,
    repo: Any | None,
    session_id: str | None,
    user_id: str | None,
    ttl_seconds: int = 1800,
) -> CrawlSnapshotStore | None:
    if repo is None:
        return None

    normalized_session_id = (
        session_id or ""
    ).strip()

    if not normalized_session_id:
        return None

    return CrawlSnapshotStore(
        repo=repo,
        session_id=normalized_session_id,
        user_id=user_id,
        ttl_seconds=ttl_seconds,
    )