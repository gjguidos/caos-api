from typing import Protocol

from src.base.queues.models import AgentVerseJob
from src.base.services.queue_service import QueueService
from src.domains.agentverse.tools.web_crawler_tool.analysis.models import (
    PageAnalysis,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.protocol import (
    AnalysisContributionBuilder,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.models import (
    ReportContributionPayload,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.models import (
    ProcessedPagePayload,
    ProcessedPageRecord,
)


class ReportJobBuilder(Protocol):
    def __call__(
        self,
        *,
        payload: ReportContributionPayload,
    ) -> AgentVerseJob:
        ...


class ParsePagePipeline:
    def __init__(
        self,
        *,
        queue_service: QueueService,
        report_queue_name: str,
        contribution_builder: AnalysisContributionBuilder,
        report_job_builder: ReportJobBuilder,
    ) -> None:
        self.queue_service = queue_service
        self.report_queue_name = report_queue_name
        self.contribution_builder = contribution_builder
        self.report_job_builder = report_job_builder

    async def handle(
        self,
        *,
        payload: ProcessedPagePayload,
        record: ProcessedPageRecord,
        analysis: PageAnalysis,
    ) -> None:
        contribution = self.contribution_builder(
            payload=payload,
            record=record,
            analysis=analysis,
        )

        await self.queue_service.enqueue(
            queue_name=self.report_queue_name,
            job=self.report_job_builder(
                payload=contribution,
            ),
        )