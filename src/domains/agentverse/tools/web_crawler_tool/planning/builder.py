# web_crawler_tool/planning/builder.py

from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlChunk,
    CrawlPlan,
    CrawlTarget,
)


def build_crawl_plan(
    *,
    root_url: str,
    urls: list[str],
    strategy: str,
    chunk_size: int,
    max_urls: int,
) -> CrawlPlan:
    targets = [
        CrawlTarget(
            url=url,
            source="sitemap",
        )
        for url in urls
    ]

    return build_crawl_plan_from_targets(
        root_url=root_url,
        targets=targets,
        strategy=strategy,
        chunk_size=chunk_size,
        max_urls=max_urls,
    )

def build_crawl_plan_from_targets(
    *,
    root_url: str,
    targets: list[CrawlTarget],
    strategy: str,
    chunk_size: int,
    max_urls: int,
) -> CrawlPlan:
    unique_targets: dict[str, CrawlTarget] = {}

    for target in targets:
        unique_targets.setdefault(
            target.url,
            target,
        )

    deduped_targets = list(
        unique_targets.values()
    )

    discovered_count = len(
        deduped_targets
    )

    selected_targets = (
        deduped_targets[:max_urls]
    )

    truncated = (
        discovered_count
        > max_urls
    )

    chunks = [
        CrawlChunk(
            index=index,
            targets=selected_targets[
                offset : offset + chunk_size
            ],
        )
        for index, offset in enumerate(
            range(
                0,
                len(selected_targets),
                chunk_size,
            ),
            start=1,
        )
    ]

    return CrawlPlan(
        root_url=root_url,
        strategy=strategy,
        targets=selected_targets,
        chunks=chunks,
        discovered_count=discovered_count,
        selected_count=len(
            selected_targets
        ),
        truncated=truncated,
        truncation_reason=(
            "max_urls"
            if truncated
            else None
        ),
    )