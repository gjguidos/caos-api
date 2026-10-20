from __future__ import annotations

from dependency_injector import containers, providers

from src.domains.agentverse.tools.web_crawler_tool.acquisition_context.acquisition_context_factory import (
    CrawlAcquisitionContextFactory,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.factory import (
    build_asset_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.persistence.factory import (
    build_crawler_db_service,
)
from src.domains.agentverse.tools.web_crawler_tool.persistence.initializer import (
    CrawlerPersistenceInitializer,
)
from src.domains.agentverse.tools.web_crawler_tool.reader.crawl_report_reader import (
    CrawlReportReader,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store_factory import (
    build_crawl_run_report_store,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.engine import (
    ReportEngine,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store_factory import (
    build_sitemap_url_inventory_store,
)


class WebCrawlerContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for the Web Crawler tool.
    """

    # ------------------------------------------------------------------
    # External dependencies
    # ------------------------------------------------------------------

    mongodb_repository = providers.Dependency()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    crawler_db_service = providers.Singleton(
        build_crawler_db_service,
        repository=mongodb_repository,
    )

    sitemap_url_inventory_store = providers.Singleton(
        build_sitemap_url_inventory_store,
        crawler_db_service=crawler_db_service,
    )

    asset_state_store = providers.Singleton(
        build_asset_state_store,
        crawler_db_service=crawler_db_service,
    )

    crawl_run_report_store = providers.Singleton(
        build_crawl_run_report_store,
        crawler_db_service=crawler_db_service,
    )

    report_engine = providers.Factory(
        ReportEngine,
        report_store=crawl_run_report_store,
        asset_store=asset_state_store,
    )

    crawl_report_reader = providers.Factory(
        CrawlReportReader,
        report_engine=report_engine,
    )

    persistence_initializer = providers.Singleton(
        CrawlerPersistenceInitializer,
        asset_store=asset_state_store,
        sitemap_store=sitemap_url_inventory_store,
        report_store=crawl_run_report_store,
    )

    acquisition_context_factory = providers.Singleton(
        CrawlAcquisitionContextFactory,
        asset_store=asset_state_store,
        sitemap_url_inventory_store=sitemap_url_inventory_store,
        crawl_run_report_store=crawl_run_report_store,
        persistence_initializer=persistence_initializer,
    )