from __future__ import annotations

from bisect import insort
from collections.abc import Iterable
from datetime import UTC, datetime

from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    SiteState,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapPlanningCoverage,
)


def _retain_candidate(
    candidates: list[str],
    url: str,
    *,
    limit: int,
) -> None:
    """
    Retain the lexicographically smallest bounded set of candidates.

    This preserves deterministic planning semantics without requiring
    the complete discovered URL universe to be globally sorted first.

    For example, with limit=3:

        current:
            /b
            /d
            /f

        incoming:
            /c

        result:
            /b
            /c
            /d

    URLs greater than or equal to the current largest retained candidate
    are ignored once the candidate buffer is full.
    """

    if limit <= 0:
        return

    if len(candidates) < limit:
        insort(
            candidates,
            url,
        )
        return

    if url >= candidates[-1]:
        return

    insort(
        candidates,
        url,
    )

    candidates.pop()


def resolve_sitemap_planning_coverage(
    *,
    state: SiteState | None,
    urls: Iterable[str],
    freshness_ttl_seconds: int,
    max_candidates: int,
) -> SitemapPlanningCoverage:
    """
    Resolve sitemap coverage specifically for bounded crawl planning.

    The complete URL universe is classified so aggregate coverage counts
    remain exact, but only a bounded number of stale and missing URLs are
    retained as acquisition candidates.

    The incoming sitemap discovery URLs are expected to already be
    normalized and deduplicated.

    Candidate retention is deterministic and independent of discovery
    order. The lexicographically smallest `max_candidates` URLs are kept
    separately for each acquisition class.

    Candidate priority is preserved downstream as:

      1. stale
      2. missing

    This allows sitemap discovery to avoid globally sorting millions of
    URLs solely for crawl planning.
    """

    now = datetime.now(
        UTC
    )

    pages = (
        state.pages
        if state is not None
        else {}
    )

    discovered_url_count = 0
    reusable_url_count = 0
    stale_url_count = 0
    missing_url_count = 0

    stale_candidates: list[str] = []
    missing_candidates: list[str] = []

    for url in urls:
        discovered_url_count += 1

        page_state = pages.get(
            url
        )

        # --------------------------------------------------------------
        # Missing.
        # --------------------------------------------------------------
        if page_state is None:
            missing_url_count += 1

            _retain_candidate(
                missing_candidates,
                url,
                limit=max_candidates,
            )

            continue

        acquired_at = (
            page_state.acquired_at
        )

        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(
                tzinfo=UTC
            )

        age_seconds = (
            now - acquired_at
        ).total_seconds()

        # --------------------------------------------------------------
        # Reusable.
        # --------------------------------------------------------------
        if (
            age_seconds
            <= freshness_ttl_seconds
        ):
            reusable_url_count += 1
            continue

        # --------------------------------------------------------------
        # Stale.
        # --------------------------------------------------------------
        stale_url_count += 1

        _retain_candidate(
            stale_candidates,
            url,
            limit=max_candidates,
        )

    return SitemapPlanningCoverage(
        discovered_url_count=(
            discovered_url_count
        ),
        reusable_url_count=(
            reusable_url_count
        ),
        stale_url_count=(
            stale_url_count
        ),
        missing_url_count=(
            missing_url_count
        ),
        stale_candidates=(
            stale_candidates
        ),
        missing_candidates=(
            missing_candidates
        ),
    )