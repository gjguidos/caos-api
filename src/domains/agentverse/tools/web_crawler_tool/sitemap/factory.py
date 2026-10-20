from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.sitemap.store import (
    SitemapStateStore,
)


def build_sitemap_state_store(
    *,
    repo: Any | None,
    session_id: str | None,
    user_id: str | None,
    ttl_seconds: int,
) -> SitemapStateStore | None:
    if repo is None:
        return None

    if not session_id:
        return None

    return SitemapStateStore()