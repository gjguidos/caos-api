from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.reader.models import (
    ReportDownload,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.engine import (
    ReportEngine,
)


class CrawlReportReader:
    """API-facing facade for crawl report downloads."""

    def __init__(
        self,
        *,
        report_engine: ReportEngine,
    ) -> None:
        self.report_engine = report_engine

    async def create_download(
        self,
        *,
        run_id: str,
        report_id: str,
    ) -> ReportDownload | None:
        return await self.report_engine.create_download(
            run_id=run_id,
            report_id=report_id,
        )
