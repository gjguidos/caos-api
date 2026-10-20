from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlChunk,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.resolve_raw_crawl import (
    resolve_raw_crawl,
)

RawPageSink = Callable[
    [CrawlTarget, FetchedPage],
    Awaitable[None],
]

@dataclass
class CrawlChunkResult:
    chunk_index: int
    planned: int
    fetched: int
    queued_for_parse: int
    failed: int
    fetched_url_hashes: list[str]
    failed_url_hashes: list[str]


async def execute_crawl_chunk(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    chunk: CrawlChunk,
    runtime: Any,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: Any,
    agent: Any,
    rate_limit_delay_ms: int,
    security: Any,
    on_page_fetched: RawPageSink,
    on_progress: Any = None,
    discover_links: bool = False,
) -> CrawlChunkResult:
    target_urls = [
        target.url
        for target in chunk.targets
    ]

    targets_by_url = {
        normalize_url(target.url): target
        for target in chunk.targets
    }
    fetched_count = 0
    failed_count = 0
    fetched_url_hashes: list[str] = []
    failed_url_hashes: list[str] = []

    async def handle_page_fetched(
        url: str,
        depth: int,
        order: int,
        page: FetchedPage,
    ) -> None:
        nonlocal fetched_count, failed_count

        del order

        target = targets_by_url.get(normalize_url(url))
        if target is None:
            if not discover_links:
                return
            target = CrawlTarget(
                url=url,
                source="link",
                depth=depth,
            )

        url_hash = target.metadata.get("url_hash")

        if page.error:
            failed_count += 1
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                (
                    "[CRAWLER][CHUNK][PAGE_FAILED] "
                    f"run_id={run_id} "
                    f"chunk_index={chunk.index} "
                    f"url={target.url} "
                    f"status_code={page.status_code} "
                    f"content_type={page.content_type!r} "
                    f"error={page.error}"
                ),
            )
            if url_hash:
                failed_url_hashes.append(url_hash)
            return

        await on_page_fetched(target, page)

        fetched_count += 1
        if url_hash:
            fetched_url_hashes.append(url_hash)

    await resolve_raw_crawl(
        runtime=runtime,
        start_url=start_url,
        fetcher=fetcher,
        auth_context=auth_context,
        agent=agent,
        rate_limit_delay_ms=rate_limit_delay_ms,
        security=security,
        target_urls=target_urls,
        on_page_fetched=handle_page_fetched,
        on_progress=on_progress,
        discover_links=discover_links,
        acquisition_concurrency=(
            cfg.page_acquisition_concurrency
        ),
    )

    return CrawlChunkResult(
        chunk_index=chunk.index,
        planned=max(
            len(target_urls),
            fetched_count + failed_count,
        ),
        fetched=fetched_count,
        queued_for_parse=fetched_count,
        failed=failed_count,
        fetched_url_hashes=fetched_url_hashes,
        failed_url_hashes=failed_url_hashes,
    )
