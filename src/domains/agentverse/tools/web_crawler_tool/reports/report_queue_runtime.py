from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from src.base.queues.models import AgentVerseJob
from src.base.queues.worker import QueueWorker, QueueWorkerPool
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.media_sink import (
    AssetStateMediaSink,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.consumer import (
    ReportContributionConsumer,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.handler import (
    ReportContributionHandler,
)


@dataclass
class ReportQueueRuntime:
    """Build, run, drain, and clean up the report contribution queue."""

    run_id: str
    queue_service: Any
    queue_name: str
    worker_pool: QueueWorkerPool | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    completed_count: int = 0
    task: asyncio.Task[None] | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        cfg: WebCrawlerToolConfig,
        queue_service: Any,
        queue_name: str,
        consumer_group: str,
        report_store: CrawlRunReportStore,
        inventory_store: Any,
        media_sink: AssetStateMediaSink,
    ) -> ReportQueueRuntime:
        report_runtime = cls(
            run_id=run_id,
            queue_service=queue_service,
            queue_name=queue_name,
        )

        consumer = ReportContributionConsumer(
            report_store=report_store,
            inventory_store=inventory_store,
            media_sink=media_sink,
        )

        async def on_complete(_job: AgentVerseJob) -> None:
            async with report_runtime.condition:
                report_runtime.completed_count += 1
                report_runtime.condition.notify_all()

        handler = ReportContributionHandler(
            consumer=consumer,
            on_complete=on_complete,
        )

        report_runtime.worker_pool = QueueWorkerPool(
            worker_count=cfg.report_workers,
            worker_factory=lambda _index: QueueWorker(
                queue_service=queue_service,
                queue_name=queue_name,
                consumer_group=consumer_group,
                handler=handler,
                stale_message_idle_ms=300_000,
                batch_size=1,
                block_ms=5_000,
            ),
        )
        return report_runtime

    def start(self) -> asyncio.Task[None]:
        if self.task is not None:
            raise RuntimeError(
                f"Report queue runtime already started for {self.run_id}"
            )
        if self.worker_pool is None:
            raise RuntimeError("Report worker pool has not been configured")

        self.task = asyncio.create_task(
            self.worker_pool.run(stop_event=self.stop_event),
            name=f"crawler-report-pool:{self.run_id}",
        )
        self.task.add_done_callback(self._log_unexpected_exit)
        return self.task

    async def wait_until_drained(self, expected_count: int) -> int:
        if self.task is None:
            raise RuntimeError("Report queue runtime has not been started")

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[WEB CRAWLER][REPORT][DRAIN_WAIT_START] "
                f"run_id={self.run_id} expected={expected_count} "
                f"completed={self.completed_count} "
                f"remaining={max(expected_count - self.completed_count, 0)}"
            ),
        )

        if self.completed_count < expected_count:
            waiter = asyncio.create_task(
                self._wait_for_count(expected_count),
                name=f"crawler-report-drain:{self.run_id}",
            )
            try:
                done, _ = await asyncio.wait(
                    {self.task, waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self.task in done:
                    async with self.condition:
                        completed = self.completed_count

                    if completed < expected_count:
                        # Propagate a worker exception if one caused exit.
                        self.task.result()
                        raise RuntimeError(
                            "Report worker pool exited before all report "
                            f"jobs completed: expected={expected_count} "
                            f"completed={completed}"
                        )

                await waiter
            finally:
                waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await waiter

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[WEB CRAWLER][REPORT][DRAIN_WAIT_COMPLETE] "
                f"run_id={self.run_id} expected={expected_count} "
                f"completed={self.completed_count}"
            ),
        )
        return self.completed_count

    async def stop_and_cleanup(self) -> None:
        if self.task is None:
            return

        self.stop_event.set()
        try:
            if not self.task.done():
                await self.task
            self.task.result()
        except BaseException:
            AGENTVERSE_LOGGER.exception(
                LogDomain.TOOL_BAY,
                f"[CRAWLER][REPORT_WORKER][FAILED] run_id={self.run_id}",
            )
            raise
        else:
            await self.queue_service.delete_queue(
                queue_name=self.queue_name,
            )

    async def _wait_for_count(self, expected_count: int) -> None:
        async with self.condition:
            await self.condition.wait_for(
                lambda: self.completed_count >= expected_count
            )

    def _log_unexpected_exit(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                f"Crawler report worker pool was cancelled: run_id={self.run_id}",
            )
            return

        exception = task.exception()
        if exception is not None:
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                (
                    "Crawler report worker pool exited with an error: "
                    f"run_id={self.run_id} "
                    f"type={type(exception).__name__} error={exception}"
                ),
            )
        elif not self.stop_event.is_set():
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                (
                    "Crawler report worker pool exited unexpectedly: "
                    f"run_id={self.run_id}"
                ),
            )
