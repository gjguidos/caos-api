from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.base.queues.models import AgentVerseJob
from src.base.services.queue_service import QueueService
from src.domains.agentverse.tools.web_crawler_tool.sitemap.chunk_window import (
    SitemapChunkWindow,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.models import (
    SitemapCrawlChunkPayload,
)


class SitemapCrawlScheduler:
    def __init__(
        self,
        *,
        inventory_store: SitemapUrlInventoryStore,
        queue_service: QueueService,
        queue_name: str,
        chunk_window: SitemapChunkWindow,
    ) -> None:
        self.inventory_store = inventory_store
        self.queue_service = queue_service
        self.queue_name = queue_name
        self.chunk_window = chunk_window

    async def schedule(
        self,
        *,
        run_id: str,
        chunk_size: int = 500,
        lease_seconds: int = 300,
        max_chunks: int | None = None,
        max_urls: int | None = None,
    ) -> int:
        if not run_id:
            raise ValueError("run_id must not be empty")

        if chunk_size <= 0:
            raise ValueError(
                "chunk_size must be greater than zero"
            )

        if lease_seconds <= 0:
            raise ValueError(
                "lease_seconds must be greater than zero"
            )

        if max_chunks is not None and max_chunks <= 0:
            raise ValueError(
                "max_chunks must be greater than zero"
            )

        if max_urls is not None and max_urls <= 0:
            raise ValueError(
                "max_urls must be greater than zero"
            )

        published_chunks = 0
        remaining_urls = max_urls

        while (
            max_chunks is None
            or published_chunks < max_chunks
        ):
            if (
                remaining_urls is not None
                and remaining_urls <= 0
            ):
                break

            # The matching handler releases this slot after the chunk
            # has completed.
            await self.chunk_window.acquire()
            
            slot_transferred = False

            batch_size = chunk_size

            if remaining_urls is not None:
                batch_size = min(
                    batch_size,
                    remaining_urls,
                )

            chunk_id = str(uuid4())
            now = datetime.now(UTC)

            try:

                records = await self.inventory_store.claim_batch(
                    run_id=run_id,
                    lease_id=chunk_id,
                    batch_size=batch_size,
                    lease_until=(
                        now
                        + timedelta(seconds=lease_seconds)
                    ),
                )

                if not records:
                    self.chunk_window.release()
                    break

                payload = SitemapCrawlChunkPayload(
                    chunk_index=published_chunks,
                    url_hashes=[
                        record.url_hash
                        for record in records
                    ],
                )

                job = AgentVerseJob(
                    schema_name=(
                        "crawler.sitemap_chunk_job.v1"
                    ),
                    job_id=str(uuid4()),
                    run_id=run_id,
                    chunk_id=chunk_id,
                    payload=payload.model_dump(
                        mode="json",
                    ),
                    created_at=now,
                )


                await self.queue_service.enqueue(
                    queue_name=self.queue_name,
                    job=job,
                )

                # The handler now owns the slot and must release it.
                slot_transferred = True

                published_chunks += 1

                if remaining_urls is not None:
                    remaining_urls -= len(records)



            except Exception:
                # If the job was successfully enqueued, the handler owns
                # the slot. Otherwise the scheduler must release it.
                if not slot_transferred:
                    self.chunk_window.release()

                raise

        return published_chunks