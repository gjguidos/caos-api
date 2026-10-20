from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.analysis.analysis_parser import (
    PageAnalysisParser,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.contributor import (
    build_analysis_contribution,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.media_page_extractor import (
    MediaPageExtractor,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.factory import (
    build_asset_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.constants import (
    report_consumer_group,
    report_queue_name,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.jobs import (
    build_report_contribution_job,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.page_processing_pipeline import (
    PageProcessingPipeline,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.parse_page_pipeline import (
    ParsePagePipeline,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.build_fetcher import (
    build_page_fetcher,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    PreparedCrawlExecution,
)
from src.domains.agentverse.tools.web_crawler_tool.http.crawler_http_client import (
    CrawlerHttpClient,
)
from src.domains.agentverse.tools.web_crawler_tool.http.factory import (
    build_crawler_http_client,
)
from src.domains.agentverse.tools.web_crawler_tool.persistence.factory import (
    build_crawler_db_service,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store_factory import (
    build_crawl_run_report_store,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.factory import (
    build_site_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.store import (
    SiteStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store_factory import (
    build_sitemap_url_inventory_store,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.factory import (
    build_crawl_snapshot_store,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.store import (
    CrawlSnapshotStore,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.constants import (
    acquisition_consumer_group,
    acquisition_queue_name,
)

logger = logging.getLogger(__name__)

@dataclass
class CrawlAcquisitionContext:
    fetcher: Any
    snapshot_store: CrawlSnapshotStore
    site_state_store: SiteStateStore
    asset_store: AssetStateStore
    sitemap_url_inventory_store: SitemapUrlInventoryStore
    crawl_run_report_store: CrawlRunReportStore
    parse_page_pipeline: ParsePagePipeline
    http_client: CrawlerHttpClient | None
    acquisition_queue_name: str
    acquisition_consumer_group: str
    page_processing_pipeline: PageProcessingPipeline
    page_parser: PageAnalysisParser
    report_queue_name: str
    report_consumer_group: str

    async def close(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()


async def close(self) -> None:
    logger.warning(
        "[CRAWLER][HTTP_CLIENT][CLOSE] run_id=%s",
        getattr(self.page_processing_pipeline, "run_id", None),
        stack_info=True,
    )

    if self.http_client is not None:
        await self.http_client.aclose()


async def build_acquisition_context(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    requested_reports: set[str] | list[str] | None,
    agent: Any,
) -> CrawlAcquisitionContext:
    
    httpx_client: CrawlerHttpClient | None = None

    if prepared.crawl_mode in {"static", "auto"}:
        httpx_client = build_crawler_http_client(
            cfg=cfg,
            timeout_seconds=runtime.timeout_seconds,
            max_connections=cfg.page_acquisition_concurrency,
        )

    queue_service = getattr(agent, "queue_service", None)
    ac_queue_name=acquisition_queue_name(run_id)
    ac_consumer_group=acquisition_consumer_group(run_id)
    rp_queue_name = report_queue_name(run_id)
    rp_consumer_group = report_consumer_group(run_id)

    fetcher = build_page_fetcher(
        cfg=cfg,
        mode=prepared.crawl_mode,
        auth_context=prepared.auth_context,
        timeout_seconds=runtime.timeout_seconds,
        max_chars_per_page=runtime.max_chars_per_page,
        http_client=httpx_client
    )

    snapshot_store = build_crawl_snapshot_store(
        repo=getattr(agent, "cache", None),
        session_id=getattr(
            agent,
            "default_session_id",
            None,
        ),
        user_id=getattr(
            agent,
            "user_id",
            None,
        ),
        ttl_seconds=cfg.snapshot_ttl_seconds,
    )

    media_extractor = MediaPageExtractor()

    page_parser = PageAnalysisParser(
        requested_reports=requested_reports,
        media_extractor=media_extractor,
        document_extractor=None,
    )

    repository = getattr(agent, "db", None)

    if repository is None:
        raise RuntimeError(
            "Web crawler requires Agent.db for asset persistence."
        )
    
    crawler_db_service = build_crawler_db_service(
        repository=repository,
    )

    if crawler_db_service is None:
        raise RuntimeError(
            "Web crawler requires crawler database persistence."
        )

    sitemap_url_inventory_store = (
        build_sitemap_url_inventory_store(
            crawler_db_service=crawler_db_service,
        )
    )

    asset_store = build_asset_state_store(
        crawler_db_service=crawler_db_service,
    )

    await sitemap_url_inventory_store.ensure_indexes()
    await asset_store.ensure_indexes()


    site_state_store = build_site_state_store(
        repo=getattr(agent, "cache", None),
        user_id=getattr(
            agent,
            "user_id",
            None,
        ),
        ttl_seconds=cfg.site_state_ttl_seconds,
    )

    crawl_run_report_store = (
        build_crawl_run_report_store(
            crawler_db_service=crawler_db_service,
        )
    )

    await crawl_run_report_store.ensure_indexes()

    parse_page_pipeline = ParsePagePipeline(
        queue_service=queue_service,
        contribution_builder=build_analysis_contribution,
        report_job_builder=build_report_contribution_job,
        report_queue_name=rp_queue_name,
    )

    page_processing_pipeline = PageProcessingPipeline(
        run_id=run_id,
        inventory_store=sitemap_url_inventory_store,
        parser=page_parser,
        on_page_analyzed=parse_page_pipeline.handle,
    )

    return CrawlAcquisitionContext(
        fetcher=fetcher,
        snapshot_store=snapshot_store,
        site_state_store=site_state_store,
        asset_store=asset_store,
        sitemap_url_inventory_store=sitemap_url_inventory_store,
        crawl_run_report_store=crawl_run_report_store,
        http_client=httpx_client,
        acquisition_queue_name=ac_queue_name,
        acquisition_consumer_group=ac_consumer_group,
        page_parser=page_parser,
        parse_page_pipeline=parse_page_pipeline,
        page_processing_pipeline=page_processing_pipeline,
        report_queue_name=rp_queue_name,
        report_consumer_group=rp_consumer_group,
    )
