from __future__ import annotations

from typing import Literal

from src.domains.agentverse.tools.web_crawler_tool.planning.builder import (
    build_crawl_plan_from_targets,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlChunk,
    CrawlPlan,
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CoverageResult,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapCoverageResult,
    SitemapPlanningCoverage,
)


def build_crawl_plan_from_coverage(
    *,
    root_url: str,
    coverage: CoverageResult,
    strategy: str,
    chunk_size: int,
    max_urls: int,
) -> CrawlPlan:
    targets = build_targets_from_coverage(
        coverage=coverage
    )

    return build_crawl_plan_from_targets(
        root_url=root_url,
        targets=targets,
        strategy=strategy,
        chunk_size=chunk_size,
        max_urls=max_urls,
    )


def build_crawl_plan_from_sitemap_coverage(
    *,
    root_url: str,
    coverage: SitemapCoverageResult,
    strategy: str,
    chunk_size: int,
    max_urls: int,
) -> CrawlPlan:
    targets = build_targets_from_sitemap_coverage(
        coverage
    )

    return build_crawl_plan_from_targets(
        root_url=root_url,
        targets=targets,
        strategy=strategy,
        chunk_size=chunk_size,
        max_urls=max_urls,
    )


def build_crawl_plan_from_sitemap_planning_coverage(
    *,
    root_url: str,
    coverage: SitemapPlanningCoverage,
    strategy: str,
    chunk_size: int,
    max_urls: int,
) -> CrawlPlan:
    """
    Build a bounded CrawlPlan from planning-oriented sitemap coverage.

    Coverage has already classified the complete discovered URL universe,
    but retains only bounded stale/missing acquisition candidates.

    Priority:
      1. stale
      2. missing
    """

    selected_urls: list[
        tuple[str, str]
    ] = []

    for url in coverage.stale_candidates:
        if len(selected_urls) >= max_urls:
            break

        selected_urls.append(
            (
                url,
                "coverage_stale",
            )
        )

    if len(selected_urls) < max_urls:
        for url in coverage.missing_candidates:
            if len(selected_urls) >= max_urls:
                break

            selected_urls.append(
                (
                    url,
                    "coverage_missing",
                )
            )

    targets = [
        CrawlTarget(
            url=url,
            source=source,
        )
        for url, source in selected_urls
    ]

    chunks = [
        CrawlChunk(
            index=index,
            targets=targets[
                offset : offset + chunk_size
            ],
        )
        for index, offset in enumerate(
            range(
                0,
                len(targets),
                chunk_size,
            ),
            start=1,
        )
    ]

    candidate_count = (
        coverage.stale_url_count
        + coverage.missing_url_count
    )

    truncated = (
        candidate_count
        > max_urls
    )

    return CrawlPlan(
        root_url=root_url,
        strategy=strategy,
        targets=targets,
        chunks=chunks,
        discovered_count=candidate_count,
        selected_count=len(
            targets
        ),
        truncated=truncated,
        truncation_reason=(
            "max_urls"
            if truncated
            else None
        ),
    )


def build_targets_from_coverage(
    *,
    coverage: CoverageResult,
) -> list[CrawlTarget]:
    targets: list[CrawlTarget] = []
    seen: set[str] = set()

    def add_targets(
        urls: list[str],
        *,
        source: Literal[
            "coverage_stale",
            "coverage_missing",
            "coverage_frontier",
        ],
    ) -> None:
        for url in urls:
            if not url:
                continue

            if url in seen:
                continue

            seen.add(
                url
            )

            targets.append(
                CrawlTarget(
                    url=url,
                    source=source,
                )
            )

    # Acquisition priority:
    #
    #   1. stale
    #   2. missing
    #   3. frontier
    #
    # Coverage itself may represent millions of URLs, but only
    # bounded actionable candidates are materialized here.
    add_targets(
        coverage.stale_candidates,
        source="coverage_stale",
    )

    add_targets(
        coverage.missing_candidates,
        source="coverage_missing",
    )

    add_targets(
        coverage.frontier_candidates,
        source="coverage_frontier",
    )

    return targets


def build_targets_from_sitemap_coverage(
    coverage: SitemapCoverageResult,
) -> list[CrawlTarget]:
    targets: list[CrawlTarget] = []

    targets.extend(
        CrawlTarget(
            url=url,
            source="coverage_stale",
        )
        for url in coverage.stale_urls
    )

    targets.extend(
        CrawlTarget(
            url=url,
            source="coverage_missing",
        )
        for url in coverage.missing_urls
    )

    return targets