from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlerCoverageMetadata,
)


def build_coverage_metadata(
    *,
    coverage: dict[str, Any] | None,
    crawl_plan: dict[str, Any] | None,
) -> CrawlerCoverageMetadata:
    if coverage is None:
        return CrawlerCoverageMetadata(
            checked=False
        )

    reusable_urls = (
        coverage.get("reusable_urls")
        or []
    )

    stale_urls = (
        coverage.get("stale_urls")
        or []
    )

    missing_urls = (
        coverage.get("missing_urls")
        or []
    )

    frontier_urls = (
        coverage.get("frontier_urls")
        or []
    )

    plan = crawl_plan or {}

    acquisition_candidates = (
        len(stale_urls)
        + len(missing_urls)
        + len(frontier_urls)
    )

    return CrawlerCoverageMetadata(
        checked=True,
        complete=bool(
            coverage.get(
                "complete",
                False,
            )
        ),
        reusable_count=len(
            reusable_urls
        ),
        stale_count=len(
            stale_urls
        ),
        missing_count=len(
            missing_urls
        ),
        frontier_count=len(
            frontier_urls
        ),
        acquisition_candidates=(
            acquisition_candidates
        ),
        planned_acquisition_count=int(
            plan.get(
                "selected_count"
            )
            or 0
        ),
        plan_truncated=bool(
            plan.get(
                "truncated",
                False,
            )
        ),
        reasons=list(
            coverage.get("reasons")
            or []
        ),
    )