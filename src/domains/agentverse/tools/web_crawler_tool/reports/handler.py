from collections.abc import Awaitable, Callable

from src.base.queues.models import AgentVerseJob
from src.domains.agentverse.tools.web_crawler_tool.reports.consumer import (
    ReportContributionConsumer,
)

ReportCompleteCallback = Callable[
    [AgentVerseJob],
    Awaitable[None],
]


class ReportContributionHandler:
    def __init__(
        self,
        *,
        consumer: ReportContributionConsumer,
        on_complete: ReportCompleteCallback | None = None,
    ) -> None:
        self.consumer = consumer
        self.on_complete = on_complete

    async def __call__(
        self,
        job: AgentVerseJob,
    ) -> None:
        await self.consumer.process_job_without_ack(
            job=job,
        )

        if self.on_complete is not None:
            await self.on_complete(job)