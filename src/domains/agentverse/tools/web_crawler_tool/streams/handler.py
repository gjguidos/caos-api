from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.base.queues.models import AgentVerseJob
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.chunk_window import (
    SitemapChunkWindow,
)

from .consumer import SitemapCrawlConsumer


class SitemapCrawlHandler:
    def __init__(
        self,
        *,
        consumer: SitemapCrawlConsumer,
        chunk_window: SitemapChunkWindow,
        on_chunk_complete: Callable[
            [CrawlChunkResult],
            Awaitable[None],
        ],
    ) -> None:
        self.consumer = consumer
        self.chunk_window = chunk_window
        self.on_chunk_complete = on_chunk_complete

    async def __call__(
        self,
        job: AgentVerseJob,
    ) -> CrawlChunkResult | None:
        try:
            result = await self.consumer.process_job_without_ack(
                job=job,
            )

            if result is not None:
                await self.on_chunk_complete(result)

            return result

        except Exception:
            AGENTVERSE_LOGGER.exception(
                LogDomain.TOOL_BAY,
                (
                    "[CONSUMER][CHUNK_FAILED] "
                    f"run_id={job.run_id} "
                    f"chunk_id={job.chunk_id}"
                ),
            )
            raise

        finally:
            self.chunk_window.release()