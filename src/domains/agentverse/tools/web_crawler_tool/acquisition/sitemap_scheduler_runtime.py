from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.chunk_window import (
    SitemapChunkWindow,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.scheduler import (
    SitemapCrawlScheduler,
)


@dataclass
class SitemapSchedulerRuntime:
    run_id: str
    scheduler: SitemapCrawlScheduler
    chunk_window: SitemapChunkWindow
    chunk_size: int
    max_chunks: int
    max_urls: int
    schedule_finished: asyncio.Event
    published_chunks: int = 0

    @classmethod
    async def create(
        cls,
        *,
        run_id: str,
        inventory_store: Any,
        queue_service: Any,
        acquisition_queue_name: str,
        acquisition_consumer_group: str,
        report_queue_name: str,
        report_consumer_group: str,
        max_in_flight: int,
        chunk_size: int,
        max_chunks: int,
        max_urls: int,
        queue_timeout_seconds: int = 10,
    ) -> SitemapSchedulerRuntime:
        chunk_window = SitemapChunkWindow(
            max_in_flight=max_in_flight,
        )

        scheduler = SitemapCrawlScheduler(
            inventory_store=inventory_store,
            queue_service=queue_service,
            queue_name=acquisition_queue_name,
            chunk_window=chunk_window,
        )

        queues = (
            (acquisition_queue_name, acquisition_consumer_group),
            (report_queue_name, report_consumer_group),
        )

        for queue_name, consumer_group in queues:
            try:
                await asyncio.wait_for(
                    queue_service.ensure_queue(
                        queue_name=queue_name,
                        consumer_group=consumer_group,
                    ),
                    timeout=queue_timeout_seconds,
                )
            except TimeoutError as exc:
                AGENTVERSE_LOGGER.error(
                    LogDomain.TOOL_BAY,
                    (
                        "[QUEUE][ENSURE_TIMEOUT] "
                        f"run_id={run_id} "
                        f"queue_name={queue_name} "
                        f"consumer_group={consumer_group}"
                    ),
                )
                raise RuntimeError(
                    f"Timed out while initializing queue {queue_name!r}."
                ) from exc

        return cls(
            run_id=run_id,
            scheduler=scheduler,
            chunk_window=chunk_window,
            chunk_size=chunk_size,
            max_chunks=max_chunks,
            max_urls=max_urls,
            schedule_finished=asyncio.Event(),
        )

    async def run(self) -> int:
        try:
            self.published_chunks = await self.scheduler.schedule(
                run_id=self.run_id,
                chunk_size=self.chunk_size,
                max_chunks=self.max_chunks,
                max_urls=self.max_urls,
            )
        finally:
            self.schedule_finished.set()

        if self.published_chunks == 0:
            raise RuntimeError(
                f"No crawler chunks were published for run {self.run_id}"
            )

        return self.published_chunks