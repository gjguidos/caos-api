from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.models import (
    CrawlAcquisitionResult,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.report_refs import (
    build_report_refs,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.media_sink import (
    AssetStateMediaSink,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.report_queue_runtime import (
    ReportQueueRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.resolve_raw_crawl import (
    resolve_raw_crawl,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
)

RawPageSink = Callable[[CrawlTarget, FetchedPage], Awaitable[None]]
PageFetchedCallback = Callable[[str, int, int, FetchedPage], Awaitable[None]]


async def acquire_site_crawl(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: Any,
    agent: Any,
    crawl_run_report_store: CrawlRunReportStore,
    asset_store: AssetStateStore,
    sitemap_url_inventory_store: SitemapUrlInventoryStore,
    operation_service: Any,
    on_page_fetched: RawPageSink,
    report_queue_name: str,
    report_consumer_group: str,
) -> CrawlAcquisitionResult:
    """Crawl a site from its root using a live, bounded link frontier."""
    queue_service = agent.queue_service
    if queue_service is None:
        raise RuntimeError(
            "Site acquisition requires Agent.queue_service."
        )

    started = perf_counter()
    fetched_count = 0
    failed_count = 0
    submitted_for_parse = 0
    root_target = CrawlTarget(
        url=start_url,
        source="manual",
    )
    targets_by_url = {
        normalize_url(start_url): root_target,
    }

    await operation_service.update_progress(
        run_id,
        phase="page_acquisition",
        current=0,
        total=runtime.max_pages,
        message="Crawling site pages.",
        metadata={
            "scope": "site",
            "limit_is_exact": False,
            "global": {
                "page_limit": runtime.max_pages,
                "acquired": 0,
                "successful": 0,
                "failed": 0,
                "elapsed_seconds": 0.0,
                "pages_per_second": 0.0,
                "eta_seconds": None,
            },
            "frontier": {
                "queued": 1,
                "visited": 0,
                "current_url": start_url,
                "depth": 0,
            },
        },
    )

    try:
        await asyncio.wait_for(
            queue_service.ensure_queue(
                queue_name=report_queue_name,
                consumer_group=report_consumer_group,
            ),
            timeout=10,
        )
    except TimeoutError as exc:
        raise RuntimeError(
            f"Timed out while initializing report queue {report_queue_name!r}."
        ) from exc

    report_runtime = ReportQueueRuntime.create(
        run_id=run_id,
        cfg=cfg,
        queue_service=queue_service,
        queue_name=report_queue_name,
        consumer_group=report_consumer_group,
        report_store=crawl_run_report_store,
        inventory_store=sitemap_url_inventory_store,
        media_sink=AssetStateMediaSink(asset_store=asset_store),
    )
    report_runtime.start()

    async def handle_page(
        url: str,
        depth: int,
        order: int,
        page: FetchedPage,
    ) -> None:
        nonlocal fetched_count, failed_count, submitted_for_parse
        del order

        target = targets_by_url.get(normalize_url(url))
        if target is None:
            target = CrawlTarget(
                url=url,
                source="link",
                depth=depth,
            )

        if page.error:
            failed_count += 1
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                (
                    "[CRAWLER][SITE][PAGE_FAILED] "
                    f"run_id={run_id} "
                    f"url={url} "
                    f"depth={depth} "
                    f"status_code={page.status_code} "
                    f"content_type={page.content_type!r} "
                    f"error={page.error}"
                ),
            )
            return

        await on_page_fetched(target, page)
        fetched_count += 1
        submitted_for_parse += 1

    async def report_progress(
        progress: CrawlAcquisitionProgress,
    ) -> None:
        elapsed = max(0.0, perf_counter() - started)
        rate = progress.current / elapsed if elapsed > 0 else 0.0
        remaining = max(runtime.max_pages - progress.current, 0)
        eta = remaining / rate if rate > 0 else None

        await operation_service.update_progress(
            run_id,
            phase="page_acquisition",
            current=progress.current,
            total=runtime.max_pages,
            message="Crawling site pages.",
            metadata={
                "scope": "site",
                "limit_is_exact": False,
                "global": {
                    "page_limit": runtime.max_pages,
                    "acquired": progress.current,
                    "successful": progress.successful,
                    "failed": progress.failed,
                    "elapsed_seconds": round(elapsed, 3),
                    "pages_per_second": round(rate, 3),
                    "eta_seconds": round(eta, 3) if eta is not None else None,
                },
                "frontier": {
                    "queued": progress.queued,
                    "visited": progress.visited,
                    "current_url": progress.current_url,
                    "depth": progress.depth,
                },
            },
        )

    try:
        await resolve_raw_crawl(
            runtime=runtime,
            start_url=start_url,
            fetcher=fetcher,
            auth_context=auth_context,
            agent=agent,
            rate_limit_delay_ms=cfg.rate_limit_delay_ms,
            security=cfg.security,
            target_urls=[start_url],
            on_page_fetched=handle_page,
            on_progress=report_progress,
            acquisition_concurrency=cfg.page_acquisition_concurrency,
            discover_links=True,
        )

        await report_runtime.wait_until_drained(submitted_for_parse)
    finally:
        try:
            if report_runtime.completed_count < submitted_for_parse:
                await report_runtime.wait_until_drained(submitted_for_parse)
        finally:
            await report_runtime.stop_and_cleanup()

    return CrawlAcquisitionResult(
        planned=fetched_count + failed_count,
        fetched=fetched_count,
        queued_for_parse=submitted_for_parse,
        failed=failed_count,
        chunks_completed=None,
        report_refs=build_report_refs(
            run_id=run_id,
            runtime=runtime,
        ),
    )
