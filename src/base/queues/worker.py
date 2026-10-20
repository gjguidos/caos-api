from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from redis.exceptions import TimeoutError as RedisTimeoutError

from src.base.queues.models import AgentVerseJob
from src.base.services.queue_service import QueueService

logger = logging.getLogger(__name__)

JobHandler = Callable[[AgentVerseJob], Awaitable[None]]


class QueueWorker:
    """Generic Redis Stream worker for AgentVerse jobs.

    A message is acknowledged only after ``handler`` completes successfully.
    Failed messages remain pending and can be reclaimed by another worker.
    Job-specific retry, lease renewal, and progress reporting belong in the
    handler, not in this queue primitive.
    """


    def __init__(
        self,
        *,
        queue_service: QueueService,
        queue_name: str,
        consumer_group: str,
        handler: JobHandler,
        stale_message_idle_ms: int = 300_000,
        stale_reclaim_interval_ms: int = 5_000,
        batch_size: int = 1,
        block_ms: int = 5_000,
    ) -> None:
        if not queue_name:
            raise ValueError("queue_name must not be empty")
        if not consumer_group:
            raise ValueError("consumer_group must not be empty")
        if stale_message_idle_ms <= 0:
            raise ValueError(
                "stale_message_idle_ms must be greater than zero"
            )
        if stale_reclaim_interval_ms <= 0:
            raise ValueError(
                "stale_reclaim_interval_ms must be greater than zero"
            )
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if block_ms <= 0:
            raise ValueError("block_ms must be greater than zero")

        self.queue_service = queue_service
        self.queue_name = queue_name
        self.consumer_group = consumer_group
        self.handler = handler
        self.stale_message_idle_ms = stale_message_idle_ms
        self.stale_reclaim_interval_seconds = (
            stale_reclaim_interval_ms / 1000.0
        )
        self.batch_size = batch_size
        self.block_ms = block_ms

    async def run(
        self,
        *,
        consumer_name: str,
        stop_event: asyncio.Event | None = None,
        max_jobs: int | None = None,
    ) -> int:
        if not consumer_name:
            raise ValueError("consumer_name must not be empty")
        if max_jobs is not None and max_jobs < 0:
            raise ValueError("max_jobs must not be negative")

        await self.queue_service.ensure_queue(
            queue_name=self.queue_name,
            consumer_group=self.consumer_group,
        )

        processed = 0
        next_stale_reclaim_at = 0.0

        while stop_event is None or not stop_event.is_set():
            if max_jobs is not None and processed >= max_jobs:
                break

            stale: list[tuple[str, AgentVerseJob]] = []
            now = asyncio.get_running_loop().time()
            if now >= next_stale_reclaim_at:
                stale = await self.queue_service.reclaim_stale(
                    queue_name=self.queue_name,
                    consumer_group=self.consumer_group,
                    consumer_name=consumer_name,
                    min_idle_ms=self.stale_message_idle_ms,
                    count=self.batch_size,
                )
                next_stale_reclaim_at = (
                    now + self.stale_reclaim_interval_seconds
                )

            # Do not block for fresh messages when reclaiming already
            # available work. This keeps recovery responsive.
            fresh: list[tuple[str, AgentVerseJob]] = []

            if not stale:
                try:
                    fresh = await self._consume_or_stop(
                        consumer_name=consumer_name,
                        stop_event=stop_event,
                    )
                except RedisTimeoutError:
                    if (
                        stop_event is not None
                        and stop_event.is_set()
                    ):
                        break

                    continue

            messages = stale + fresh

            if max_jobs is not None:
                messages = messages[: max_jobs - processed]

            for message_id, job in messages:
                await self.handler(job)
                await self.queue_service.acknowledge(
                    queue_name=self.queue_name,
                    consumer_group=self.consumer_group,
                    message_id=message_id,
                )
                processed += 1

        return processed
    
    async def _consume_or_stop(
        self,
        *,
        consumer_name: str,
        stop_event: asyncio.Event | None,
    ) -> list[tuple[str, AgentVerseJob]]:
        consume_task = asyncio.create_task(
            self.queue_service.consume(
                queue_name=self.queue_name,
                consumer_group=self.consumer_group,
                consumer_name=consumer_name,
                count=self.batch_size,
                block_ms=self.block_ms,
            )
        )

        if stop_event is None:
            return await consume_task

        stop_task = asyncio.create_task(
            stop_event.wait()
        )

        done, _ = await asyncio.wait(
            {consume_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if consume_task in done:
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task

            return consume_task.result()

        consume_task.cancel()
        with suppress(asyncio.CancelledError):
            await consume_task

        return []


class QueueWorkerPool:
    """Runs several generic workers in the same Redis consumer group."""

    def __init__(
        self,
        *,
        worker_factory: Callable[[int], QueueWorker],
        worker_count: int,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be greater than zero")

        self.worker_factory = worker_factory
        self.worker_count = worker_count

    async def run(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        max_jobs: int | None = None,
    ) -> int:
        worker_stop = (
            stop_event
            if stop_event is not None
            else asyncio.Event()
        )

        counts = [0] * self.worker_count

        async def run_worker(index: int) -> None:
            worker = self.worker_factory(index)
            counts[index] = await worker.run(
                consumer_name=f"worker-{index}",
                stop_event=worker_stop,
                max_jobs=max_jobs,
            )

        tasks = [
            asyncio.create_task(
                run_worker(index),
                name=f"queue-worker-{index}",
            )
            for index in range(self.worker_count)
        ]

        try:
            await asyncio.gather(*tasks)

        except BaseException:
            worker_stop.set()

            for task in tasks:
                if not task.done():
                    task.cancel()

            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

            raise

        return (
            min(sum(counts), max_jobs)
            if max_jobs is not None
            else sum(counts)
        )
