from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.acquisition.models import (
    CrawlAcquisitionResult,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.sitemap_acquisition import (
    acquire_sitemap_crawl,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.site_acquisition import (
    acquire_site_crawl,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlPlan,
)

RawPageSink = Callable[[CrawlTarget, FetchedPage], Awaitable[None]]


async def acquire_crawl(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: Any,
    agent: Any,
    sitemap_url_inventory_store: Any,
    crawl_run_report_store: CrawlRunReportStore,
    asset_store: AssetStateStore,
    operation_service: Any,
    on_page_fetched: RawPageSink,
    acquisition_queue_name: str,
    acquisition_consumer_group: str,
    report_queue_name: str,
    report_consumer_group: str,
    crawl_plan: CrawlPlan | None = None,
) -> CrawlAcquisitionResult:
    if runtime.crawl_scope == "sitemap":
        if sitemap_url_inventory_store is None:
            raise RuntimeError(
                "Sitemap acquisition requires a sitemap URL inventory store."
            )
        return await acquire_sitemap_crawl(
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            start_url=start_url,
            crawl_mode=crawl_mode,
            fetcher=fetcher,
            auth_context=auth_context,
            agent=agent,
            sitemap_url_inventory_store=sitemap_url_inventory_store,
            crawl_run_report_store=crawl_run_report_store,
            asset_store=asset_store,
            operation_service=operation_service,
            on_page_fetched=on_page_fetched,
            acquisition_queue_name=acquisition_queue_name,
            acquisition_consumer_group=acquisition_consumer_group,
            report_queue_name=report_queue_name,
            report_consumer_group=report_consumer_group,
        )

    if runtime.crawl_scope == "site":
        return await acquire_site_crawl(
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            start_url=start_url,
            crawl_mode=crawl_mode,
            fetcher=fetcher,
            auth_context=auth_context,
            agent=agent,
            crawl_run_report_store=crawl_run_report_store,
            asset_store=asset_store,
            sitemap_url_inventory_store=sitemap_url_inventory_store,
            operation_service=operation_service,
            on_page_fetched=on_page_fetched,
            report_queue_name=report_queue_name,
            report_consumer_group=report_consumer_group,
        )

    raise ValueError(f"Unsupported crawl scope: {runtime.crawl_scope}")
