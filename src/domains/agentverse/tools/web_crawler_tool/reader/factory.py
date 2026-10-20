from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.asset_state.factory import (
    build_asset_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.reader.crawl_report_reader import (
    CrawlReportReader,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store_factory import (
    build_crawl_run_report_store,
)

if TYPE_CHECKING:
    from src.domains.agentverse.tools.web_crawler_tool.persistence.crawler_db_service import (
        CrawlerDBService,
    )


def build_crawl_report_reader(
    *,
    crawler_db_service: CrawlerDBService,
) -> CrawlReportReader:
    """Build a request-scoped reader over the crawler's shared DB service."""
    return CrawlReportReader(
        report_store=build_crawl_run_report_store(
            crawler_db_service=crawler_db_service,
        ),
        asset_store=build_asset_state_store(
            crawler_db_service=crawler_db_service,
        ),
    )

