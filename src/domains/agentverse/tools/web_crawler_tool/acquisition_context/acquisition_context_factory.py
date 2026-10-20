from __future__ import annotations

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
from src.domains.agentverse.tools.web_crawler_tool.persistence.initializer import (
    CrawlerPersistenceInitializer,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.factory import (
    build_site_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.factory import (
    build_crawl_snapshot_store,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.constants import (
    acquisition_consumer_group,
    acquisition_queue_name,
)

from .acquisition_context import CrawlAcquisitionContext


class CrawlAcquisitionContextFactory:
    def __init__(
        self,
        *,
        asset_store: AssetStateStore,
        sitemap_url_inventory_store: SitemapUrlInventoryStore,
        crawl_run_report_store: CrawlRunReportStore,
        persistence_initializer: CrawlerPersistenceInitializer,
    ) -> None:
        self.asset_store = asset_store
        self.sitemap_url_inventory_store = sitemap_url_inventory_store
        self.crawl_run_report_store = crawl_run_report_store
        self.persistence_initializer = persistence_initializer

    async def build(
        self,
        *,
        run_id: str,
        cfg: WebCrawlerToolConfig,
        runtime: ResolvedCrawlerRuntime,
        prepared: PreparedCrawlExecution,
        requested_reports: set[str] | list[str] | None,
        agent: Any,
    ) -> CrawlAcquisitionContext:
        await self.persistence_initializer.ensure_initialized()

        http_client: CrawlerHttpClient | None = None

        if prepared.crawl_mode in {"static", "auto"}:
            http_client = build_crawler_http_client(
                cfg=cfg,
                timeout_seconds=runtime.timeout_seconds,
                max_connections=cfg.page_acquisition_concurrency,
            )

        queue_service = getattr(agent, "queue_service", None)

        ac_queue_name = acquisition_queue_name(run_id)
        ac_consumer_group = acquisition_consumer_group(run_id)
        rp_queue_name = report_queue_name(run_id)
        rp_consumer_group = report_consumer_group(run_id)

        fetcher = build_page_fetcher(
            cfg=cfg,
            mode=prepared.crawl_mode,
            auth_context=prepared.auth_context,
            timeout_seconds=runtime.timeout_seconds,
            max_chars_per_page=runtime.max_chars_per_page,
            http_client=http_client,
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

        site_state_store = build_site_state_store(
            repo=getattr(agent, "cache", None),
            user_id=getattr(
                agent,
                "user_id",
                None,
            ),
            ttl_seconds=cfg.site_state_ttl_seconds,
        )

        page_parser = PageAnalysisParser(
            requested_reports=requested_reports,
            media_extractor=MediaPageExtractor(),
            document_extractor=None,
        )

        parse_page_pipeline = ParsePagePipeline(
            queue_service=queue_service,
            contribution_builder=build_analysis_contribution,
            report_job_builder=build_report_contribution_job,
            report_queue_name=rp_queue_name,
        )

        page_processing_pipeline = PageProcessingPipeline(
            run_id=run_id,
            inventory_store=self.sitemap_url_inventory_store,
            parser=page_parser,
            on_page_analyzed=parse_page_pipeline.handle,
        )

        return CrawlAcquisitionContext(
            fetcher=fetcher,
            snapshot_store=snapshot_store,
            site_state_store=site_state_store,
            asset_store=self.asset_store,
            sitemap_url_inventory_store=self.sitemap_url_inventory_store,
            crawl_run_report_store=self.crawl_run_report_store,
            parse_page_pipeline=parse_page_pipeline,
            http_client=http_client,
            acquisition_queue_name=ac_queue_name,
            acquisition_consumer_group=ac_consumer_group,
            page_processing_pipeline=page_processing_pipeline,
            page_parser=page_parser,
            report_queue_name=rp_queue_name,
            report_consumer_group=rp_consumer_group,
        )