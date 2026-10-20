from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.base.queues.models import AgentVerseJob
from src.base.services.queue_service import QueueService
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
    execute_crawl_chunk,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlChunk,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.models import (
    SitemapCrawlChunkPayload,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
)

logger = logging.getLogger(__name__)

RawPageSink = Callable[
    [CrawlTarget, FetchedPage],
    Awaitable[None],
]

CRAWLER_ACQUISITION_SCHEMA = (
    "crawler.sitemap_chunk_job.v1"
)


class SitemapCrawlConsumer:
    """Processes sitemap chunk jobs from Redis Streams."""

    def __init__(
        self,
        *,
        run_id: str,
        acquisition_started: float,
        accumulator: Any,
        planned_total: int,
        queue_service: QueueService,
        inventory_store: SitemapUrlInventoryStore,
        cfg: WebCrawlerToolConfig,
        runtime: ResolvedCrawlerRuntime,
        start_url: str,
        crawl_mode: str,
        fetcher: Any,
        auth_context: Any,
        agent: Any,
        on_page_fetched: RawPageSink,
        queue_name: str,
        consumer_group: str,
        lease_seconds: int = 300,
        lease_renew_interval_seconds: int = 60,
        on_chunk_complete: Callable[
            [CrawlChunkResult, int, int, int, int, int],
            Awaitable[None],
        ] | None = None,
        on_chunk_progress: Callable[
            [CrawlAcquisitionProgress, int, int, int, int, int, int],
            Awaitable[None],
        ] | None = None,
        operation_service: Any = None,
    ) -> None:
        self.run_id = run_id
        self.acquisition_started = acquisition_started
        self.accumulator = accumulator
        self.planned_total = planned_total
        self.queue_service = queue_service
        self.inventory_store = inventory_store
        self.cfg = cfg
        self.runtime = runtime
        self.start_url = start_url
        self.crawl_mode = crawl_mode
        self.fetcher = fetcher
        self.auth_context = auth_context
        self.agent = agent
        self.on_page_fetched = on_page_fetched
        self.queue_name = queue_name
        self.consumer_group = consumer_group
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = (
            lease_renew_interval_seconds
        )
        self.on_chunk_complete = on_chunk_complete
        self.on_chunk_progress = on_chunk_progress
        self.operation_service = operation_service


    async def run(
        self,
        *,
        consumer_name: str,
        stop_event: asyncio.Event | None = None,
        max_jobs: int | None = None,
    ) -> list[CrawlChunkResult]:
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "Starting sitemap consumer "
                f"run_id={self.run_id} "
                f"queue={self.queue_name} "
                f"group={self.consumer_group} "
                f"consumer={consumer_name}"
            ),
        )
                
        if not consumer_name:
            raise ValueError(
                "consumer_name must not be empty"
            )

        await self.queue_service.ensure_queue(
            queue_name=self.queue_name,
            consumer_group=self.consumer_group,
        )

        results: list[CrawlChunkResult] = []

        while (
            stop_event is None
            or not stop_event.is_set()
        ):
            if (
                max_jobs is not None
                and len(results) >= max_jobs
            ):
                break

            messages = await self.queue_service.consume(
                queue_name=self.queue_name,
                consumer_group=self.consumer_group,
                consumer_name=consumer_name,
                count=1,
                block_ms=5_000,
            )

            for message_id, job in messages:
                completed_planned = sum(
                    item.planned
                    for item in results
                )
                completed_acquired = sum(
                    item.fetched
                    for item in results
                )
                completed_successful = sum(
                    item.queued_for_parse
                    for item in results
                )
                completed_failed = sum(
                    item.failed
                    for item in results
                )

                result = await self.process_job(
                    message_id=message_id,
                    job=job,
                    completed_chunks=len(results),
                    completed_planned=completed_planned,
                    completed_acquired=completed_acquired,
                    completed_successful=completed_successful,
                    completed_failed=completed_failed,
                )

                if result is not None:
                    results.append(result)

                    if self.on_chunk_complete is not None:
                        await self.on_chunk_complete(
                            result,
                            len(results),
                            sum(item.planned for item in results),
                            sum(item.fetched for item in results),
                            sum(
                                item.queued_for_parse
                                for item in results
                            ),
                            sum(item.failed for item in results),
                        )

                if (
                    max_jobs is not None
                    and len(results) >= max_jobs
                ):
                    break

        return results
    
    async def process_job(
        self,
        *,
        message_id: str,
        job: AgentVerseJob,
        completed_chunks: int = 0,
        completed_planned: int = 0,
        completed_acquired: int = 0,
        completed_successful: int = 0,
        completed_failed: int = 0,
    ) -> CrawlChunkResult | None:
        result = await self.process_job_without_ack(
            job=job,
            completed_chunks=completed_chunks,
            completed_planned=completed_planned,
            completed_acquired=completed_acquired,
            completed_successful=completed_successful,
            completed_failed=completed_failed,
        )


        await self._acknowledge(message_id)

        return result

    async def process_job_without_ack(
        self,
        *,
        job: AgentVerseJob,
        completed_chunks: int = 0,
        completed_planned: int = 0,
        completed_acquired: int = 0,
        completed_successful: int = 0,
        completed_failed: int = 0,
    ) -> CrawlChunkResult | None:
        if job.run_id != self.run_id:
            raise RuntimeError(
                "Crawler consumer received a job for another run: "
                f"expected={self.run_id} "
                f"received={job.run_id} "
                f"queue={self.queue_name}"
            )

        if job.schema_name != CRAWLER_ACQUISITION_SCHEMA:
            raise ValueError(
                f"Unsupported crawler job schema: {job.schema_name}"
            )

        if not job.run_id:
            raise ValueError(
                "Sitemap crawl chunk job requires run_id"
            )

        if not job.chunk_id:
            raise ValueError(
                "Sitemap crawl chunk job requires chunk_id"
            )

        payload = SitemapCrawlChunkPayload.model_validate(
            job.payload
        )

        records = await (
            self.inventory_store
            .get_leased_urls_by_hashes(
                run_id=job.run_id,
                lease_id=job.chunk_id,
                url_hashes=payload.url_hashes,
            )
        )

        if not records:
            return None

        chunk = CrawlChunk(
            index=payload.chunk_index,
            targets=[
                CrawlTarget(
                    url=record.url,
                    source="sitemap",
                    lease_id=job.chunk_id,
                    depth=0,
                    metadata={
                        "url_hash": record.url_hash,
                        "source_sitemap_url": (
                            record.source_sitemap_url
                        ),
                        "discovery_order": (
                            record.discovery_order
                        ),
                    },
                )
                for record in records
            ],
        )
        
        heartbeat_task = asyncio.create_task(
            self._renew_lease_until_complete(
                run_id=job.run_id,
                lease_id=job.chunk_id,
            )
        )

        async def report_progress(
            progress: CrawlAcquisitionProgress,
        ) -> None:
            if self.on_chunk_progress is None:
                return

            await self.on_chunk_progress(
                progress,
                payload.chunk_index,
                completed_chunks,
                completed_planned,
                completed_acquired,
                completed_successful,
                completed_failed,
            )

        try:
            chunk_result = await execute_crawl_chunk(
                run_id=job.run_id,
                cfg=self.cfg,
                chunk=chunk,
                runtime=self.runtime,
                start_url=self.start_url,
                crawl_mode=self.crawl_mode,
                fetcher=self.fetcher,
                auth_context=self.auth_context,
                agent=self.agent,
                rate_limit_delay_ms=self.cfg.rate_limit_delay_ms,
                security=self.cfg.security,
                on_page_fetched=self.on_page_fetched,
                on_progress=report_progress,
            )

        finally:
            heartbeat_task.cancel()

            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        return chunk_result

        
    async def _renew_lease_until_complete(
        self,
        *,
        run_id: str,
        lease_id: str,
    ) -> None:
        while True:
            await asyncio.sleep(
                self.lease_renew_interval_seconds
            )

            lease_until = (
                datetime.now(UTC)
                + timedelta(seconds=self.lease_seconds)
            )

            renewed = await (
                self.inventory_store
                .renew_sitemap_url_batch(
                    run_id=run_id,
                    lease_id=lease_id,
                    lease_until=lease_until,
                )
            )

            if renewed == 0:
                raise RuntimeError(
                    "Crawler URL lease was lost while processing "
                    f"run_id={run_id} lease_id={lease_id}"
                )

            logger.debug(
                "Renewed crawler URL lease "
                "run_id=%s lease_id=%s records=%d "
                "lease_until=%s",
                run_id,
                lease_id,
                renewed,
                lease_until.isoformat(),
            )

    async def _acknowledge(
        self,
        message_id: str,
    ) -> None:
        await self.queue_service.acknowledge(
            queue_name=self.queue_name,
            consumer_group=self.consumer_group,
            message_id=message_id,
        )
