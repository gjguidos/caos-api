from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlparse

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.snapshot import (
    build_page_snapshot,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.graph import (
    merge_crawl_graphs,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CoverageObservation,
    PageState,
    SiteState,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.store import (
    SiteStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.resolver import (
    ResolvedCrawlEvidence,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlResult,
)


def update_site_state(
    *,
    existing: SiteState | None,
    result: CrawlResult,
    record_frontier_coverage: bool = True,
    max_depth: int,
    max_pages: int,
) -> SiteState:
    now = datetime.now(UTC)

    domain = (
        urlparse(result.start_url).hostname or ""
    ).lower()

    state = existing or SiteState(domain=domain)
    graph = result.crawl_graph

    depth_by_url: dict[str, int] = {}

    if graph:
        depth_by_url = {
            node.url: node.depth
            for node in graph.nodes
        }

    for page in result.pages:
        url = normalize_url(
            page.final_url or page.url
        )

        previous = state.pages.get(url)

        page_snapshot = build_page_snapshot(
            page=page.model_dump()
        )

        state.pages[url] = PageState(
            url=url,
            first_seen_at=(
                previous.first_seen_at
                if previous
                else now
            ),
            last_seen_at=now,
            acquired_at=now,
            depth=depth_by_url.get(
                page.url,
                0,
            ),
            status=(
                "error"
                if page.error
                else "visited"
            ),
            snapshot=page_snapshot,
            page=page,
        )

    # Merge once per crawl, never once per page.
    if graph:
        state.crawl_graph = merge_crawl_graphs(
            existing=state.crawl_graph,
            incoming=graph,
            start_url=result.start_url,
        )

    if record_frontier_coverage:
        truncated = _is_crawl_truncated(
            result=result,
            max_pages=max_pages,
        )

        state.coverage_observations.append(
            CoverageObservation(
                root_url=normalize_url(
                    result.start_url
                ),
                max_depth=max_depth,
                max_pages=max_pages,
                completed=not truncated,
                truncated=truncated,
                acquired_at=now,
            )
        )

    state.updated_at = now
    return state


async def update_site_state_from_evidence(
    *,
    store: SiteStateStore | None,
    evidence: ResolvedCrawlEvidence,
    record_frontier_coverage: bool = True,
    start_url: str,
    max_depth: int,
    max_pages: int,
) -> None:
    if store is None or evidence.reused:
        return

    domain = (
        urlparse(start_url).hostname or ""
    ).lower()

    if not domain:
        return

    get_started = perf_counter()

    existing = await store.get(domain)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER][SITE_STATE]"
            "[GET_COMPLETE] "
            f"domain={domain} "
            f"seconds={perf_counter() - get_started:.3f}"
        ),
    )

    update_started = perf_counter()

    updated = await asyncio.to_thread(
        update_site_state,
        existing=existing,
        result=evidence.result,
        max_depth=max_depth,
        max_pages=max_pages,
        record_frontier_coverage=record_frontier_coverage,
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER][SITE_STATE]"
            "[UPDATE_COMPLETE] "
            f"domain={domain} "
            f"pages={len(updated.pages)} "
            f"seconds={perf_counter() - update_started:.3f}"
        ),
    )

    put_started = perf_counter()

    await store.put(updated)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER][SITE_STATE]"
            "[PUT_COMPLETE] "
            f"domain={domain} "
            f"seconds={perf_counter() - put_started:.3f}"
        ),
    )


def _is_crawl_truncated(
    *,
    result: CrawlResult,
    max_pages: int,
) -> bool:
    graph = result.crawl_graph

    if graph is None:
        return True

    if graph.queued_count > 0:
        return True

    return result.page_count >= max_pages