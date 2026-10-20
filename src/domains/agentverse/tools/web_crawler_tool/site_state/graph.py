from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_graph import (
    CrawlGraphBuilder,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlGraphReport,
)


def merge_crawl_graphs(
    *,
    existing: CrawlGraphReport | None,
    incoming: CrawlGraphReport,
    start_url: str,
) -> CrawlGraphReport:
    builder = CrawlGraphBuilder.from_report(
        start_url=start_url,
        report=existing,
    )

    builder.merge_report(
        incoming
    )

    return builder.build()