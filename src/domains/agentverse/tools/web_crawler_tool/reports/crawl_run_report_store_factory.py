from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)

if TYPE_CHECKING:
    from src.domains.agentverse.tools.web_crawler_tool.persistence.crawler_db_service import (
        CrawlerDBService,
    )


def build_crawl_run_report_store(
    *,
    crawler_db_service: CrawlerDBService,
) -> CrawlRunReportStore:
    return CrawlRunReportStore(
        db_service=crawler_db_service,
    )