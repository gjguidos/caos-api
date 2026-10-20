from __future__ import annotations

from typing import Protocol

from src.domains.agentverse.tools.web_crawler_tool.reader.models import (
    ReportDownload,
)


class CrawlReportReaderInterface(Protocol):
    async def create_download(
        self,
        *,
        run_id: str,
        report_id: str,
    ) -> ReportDownload | None:
        """Build a streamed download for a report in a crawl run."""
        ...

