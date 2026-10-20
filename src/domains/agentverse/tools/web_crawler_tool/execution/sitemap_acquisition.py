from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from math import ceil
from time import perf_counter
from typing import Any
from uuid import uuid4

from src.domains.agentverse.tools.web_crawler_tool.acquisition.models import (
    CrawlAcquisitionResult,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.progress import (
    report_queued_chunk_complete,
    report_queued_chunk_progress,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.report_refs import (
    build_report_refs as _build_report_refs,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.sitemap_scheduler_runtime import (
    SitemapSchedulerRuntime,
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
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_run_accumulator import (
    CrawlRunAccumulator,
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
from src.domains.agentverse.tools.web_crawler_tool.sitemap.sitemap_queue_runtime import (
    SitemapQueueRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.context import (
    SitemapCrawlStreamContext,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
)

RawPageSink = Callable[[CrawlTarget, FetchedPage], Awaitable[None]]


async def acquire_sitemap_crawl(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: Any,
    agent: Any,
    sitemap_url_inventory_store: Any,
    crawl_run_report_store: CrawlRunReportStore,
    asset_store: AssetStateStore,
    operation_service: Any,
    on_page_fetched: RawPageSink,
    acquisition_queue_name: str,
    acquisition_consumer_group: str,
    report_queue_name: str,
    report_consumer_group: str,
) -> CrawlAcquisitionResult:
    stop_event = asyncio.Event()
    accumulator = CrawlRunAccumulator()
    acquisition_started = perf_counter()
    queue_service = agent.queue_service

    if queue_service is None:
        raise RuntimeError(
            "Crawler acquisition requires Agent.queue_service."
        )

    inventory_count = await sitemap_url_inventory_store.count(
        run_id=run_id,
        status="discovered",
    )
    planned_total = (
        min(inventory_count, runtime.page_acquisition_limit)
        if runtime.page_acquisition_limit is not None
        else inventory_count
    )
    chunk_size = cfg.crawl_plan_chunk_size
    total_chunks = ceil(planned_total / chunk_size) if planned_total else 0

    stream_context = SitemapCrawlStreamContext(
        run_id=run_id,
        queue_name=acquisition_queue_name,
        consumer_group=acquisition_consumer_group,
        consumer_name=f"consumer-{uuid4()}",
    )

    # Publish the phase before queue work starts so clients leave sitemap
    # discovery and display page-acquisition progress.
    await operation_service.update_progress(
        run_id,
        phase="page_acquisition",
        current=0,
        total=planned_total,
        message="Crawling sitemap pages.",
        metadata={
            "scope": "sitemap",
            "global": {
                "planned": planned_total,
                "acquired": 0,
                "successful": 0,
                "failed": 0,
                "chunks_completed": 0,
                "total_chunks": total_chunks,
                "elapsed_seconds": 0.0,
                "pages_per_second": 0.0,
                "eta_seconds": None,
            },
            "chunk": {
                "index": None,
                "planned": 0,
                "acquired": 0,
                "successful": 0,
                "failed": 0,
            },
        },
    )

    scheduler_runtime = await SitemapSchedulerRuntime.create(
        run_id=run_id,
        inventory_store=sitemap_url_inventory_store,
        queue_service=queue_service,
        acquisition_queue_name=acquisition_queue_name,
        acquisition_consumer_group=acquisition_consumer_group,
        report_queue_name=report_queue_name,
        report_consumer_group=report_consumer_group,
        max_in_flight=cfg.max_in_flight_chunks,
        chunk_size=chunk_size,
        max_chunks=total_chunks,
        max_urls=planned_total,
    )

    media_sink = AssetStateMediaSink(asset_store=asset_store)

    async def on_chunk_complete(result: CrawlChunkResult) -> None:
        await accumulator.record(result)
        totals = await accumulator.totals()
        completed_chunks = len(await accumulator.results())

        await report_queued_chunk_complete(
            run_id,
            acquisition_started,
            planned_total,
            result,
            total_chunks,
            completed_chunks,
            totals["planned"],
            totals["fetched"],
            totals["queued_for_parse"],
            totals["failed"],
            operation_service,
        )

        published_chunks = scheduler_runtime.published_chunks
        if (
            scheduler_runtime.schedule_finished.is_set()
            and published_chunks > 0
            and completed_chunks >= published_chunks
        ):
            stop_event.set()

    async def on_chunk_progress(
        progress: CrawlAcquisitionProgress,
        chunk_index: int,
        completed_chunks: int,
        completed_planned: int,
        completed_acquired: int,
        completed_successful: int,
        completed_failed: int,
    ) -> None:
        await report_queued_chunk_progress(
            run_id=run_id,
            acquisition_started=acquisition_started,
            accumulator=accumulator,
            progress=progress,
            planned_total=planned_total,
            chunk_index=chunk_index,
            completed_chunks=completed_chunks,
            completed_planned=completed_planned,
            completed_acquired=completed_acquired,
            completed_successful=completed_successful,
            completed_failed=completed_failed,
            operation_service=operation_service,
        )

    sitemap_queue_runtime = SitemapQueueRuntime.create(
        run_id=run_id,
        cfg=cfg,
        runtime=runtime,
        start_url=start_url,
        crawl_mode=crawl_mode,
        fetcher=fetcher,
        auth_context=auth_context,
        agent=agent,
        inventory_store=sitemap_url_inventory_store,
        queue_service=queue_service,
        stream_context=stream_context,
        scheduler_runtime=scheduler_runtime,
        acquisition_started=acquisition_started,
        accumulator=accumulator,
        planned_total=planned_total,
        on_page_fetched=on_page_fetched,
        on_chunk_complete=on_chunk_complete,
        on_chunk_progress=on_chunk_progress,
        operation_service=operation_service,
    )

    report_queue_runtime = ReportQueueRuntime.create(
        run_id=run_id,
        cfg=cfg,
        queue_service=queue_service,
        queue_name=report_queue_name,
        consumer_group=report_consumer_group,
        report_store=crawl_run_report_store,
        inventory_store=sitemap_url_inventory_store,
        media_sink=media_sink,
    )

    report_queue_runtime.start()
    acquisition_task = asyncio.create_task(
        sitemap_queue_runtime.run(stop_event=stop_event),
        name=f"crawler-acquisition-pool:{run_id}",
    )
    schedule_task = asyncio.create_task(
        scheduler_runtime.run(),
        name=f"crawler-scheduler:{run_id}",
    )

    try:
        await schedule_task

        published_chunks = scheduler_runtime.published_chunks
        completed_chunks = len(await accumulator.results())
        if published_chunks > 0 and completed_chunks >= published_chunks:
            stop_event.set()

        await acquisition_task

        totals = await accumulator.totals()
        expected_report_count = totals["queued_for_parse"]
        await report_queue_runtime.wait_until_drained(
            expected_report_count,
        )
    except BaseException:
        stop_event.set()
        if not schedule_task.done():
            schedule_task.cancel()
        if not acquisition_task.done():
            acquisition_task.cancel()
        await asyncio.gather(
            schedule_task,
            acquisition_task,
            return_exceptions=True,
        )
        raise
    finally:
        await report_queue_runtime.stop_and_cleanup()

    chunk_results = await accumulator.results()
    if not chunk_results:
        raise RuntimeError(
            "Queued sitemap crawl produced no chunk results."
        )

    return CrawlAcquisitionResult(
        planned=sum(result.planned for result in chunk_results),
        fetched=sum(result.fetched for result in chunk_results),
        queued_for_parse=sum(
            result.queued_for_parse for result in chunk_results
        ),
        failed=sum(result.failed for result in chunk_results),
        chunks_completed=len(chunk_results),
        report_refs=_build_report_refs(
            run_id=run_id,
            runtime=runtime,
        ),
    )
