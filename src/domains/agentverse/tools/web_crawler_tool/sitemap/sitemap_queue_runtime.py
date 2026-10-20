from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.base.queues.worker import (
    QueueWorker,
    QueueWorkerPool,
)
from src.domains.agentverse.tools.web_crawler_tool.acquisition.sitemap_scheduler_runtime import (
    SitemapSchedulerRuntime,
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
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.chunk_window import (
    SitemapChunkWindow,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.consumer import (
    SitemapCrawlConsumer,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.context import (
    SitemapCrawlStreamContext,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.handler import (
    SitemapCrawlHandler,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
)

RawPageSink = Callable[[CrawlTarget, FetchedPage], Awaitable[None]]
ChunkCompleteCallback = Callable[[CrawlChunkResult], Awaitable[None]]
ChunkProgressCallback = Callable[
    [CrawlAcquisitionProgress, int, int, int, int, int, int],
    Awaitable[None],
]


@dataclass
class SitemapQueueRuntime:
    """Build and run the sitemap acquisition consumer and worker pool."""

    worker_pool: QueueWorkerPool
    chunk_window: SitemapChunkWindow

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        cfg: WebCrawlerToolConfig,
        runtime: ResolvedCrawlerRuntime,
        start_url: str,
        crawl_mode: str,
        fetcher: Any,
        auth_context: Any,
        agent: Any,
        inventory_store: Any,
        queue_service: Any,
        stream_context: SitemapCrawlStreamContext,
        scheduler_runtime: SitemapSchedulerRuntime,
        acquisition_started: float,
        accumulator: CrawlRunAccumulator,
        planned_total: int,
        on_page_fetched: RawPageSink,
        on_chunk_complete: ChunkCompleteCallback,
        on_chunk_progress: ChunkProgressCallback,
        operation_service: Any,
    ) -> SitemapQueueRuntime:
        consumer = SitemapCrawlConsumer(
            run_id=run_id,
            acquisition_started=acquisition_started,
            accumulator=accumulator,
            planned_total=planned_total,
            queue_service=queue_service,
            inventory_store=inventory_store,
            cfg=cfg,
            runtime=runtime,
            start_url=start_url,
            crawl_mode=crawl_mode,
            fetcher=fetcher,
            auth_context=auth_context,
            agent=agent,
            on_page_fetched=on_page_fetched,
            queue_name=stream_context.queue_name,
            consumer_group=stream_context.consumer_group,
            # Completion is invoked by SitemapCrawlHandler after the consumer
            # has processed the job.
            on_chunk_complete=None,
            on_chunk_progress=on_chunk_progress,
            operation_service=operation_service,
        )

        handler = SitemapCrawlHandler(
            consumer=consumer,
            chunk_window=scheduler_runtime.chunk_window,
            on_chunk_complete=on_chunk_complete,
        )

        worker_pool = QueueWorkerPool(
            worker_count=cfg.crawler_workers,
            worker_factory=lambda _index: QueueWorker(
                queue_service=queue_service,
                queue_name=stream_context.queue_name,
                consumer_group=stream_context.consumer_group,
                handler=handler,
                stale_message_idle_ms=300_000,
                batch_size=1,
                block_ms=5_000,
            ),
        )

        return cls(
            worker_pool=worker_pool,
            chunk_window=scheduler_runtime.chunk_window,
        )

    async def run(self, *, stop_event: asyncio.Event) -> None:
        await self.worker_pool.run(stop_event=stop_event)
