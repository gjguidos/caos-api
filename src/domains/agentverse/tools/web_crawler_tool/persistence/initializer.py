from __future__ import annotations

import asyncio

from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)


class CrawlerPersistenceInitializer:
    def __init__(
        self,
        *,
        asset_store: AssetStateStore,
        sitemap_store: SitemapUrlInventoryStore,
        report_store: CrawlRunReportStore,
    ) -> None:
        self.asset_store = asset_store
        self.sitemap_store = sitemap_store
        self.report_store = report_store

        self._initialized = False
        self._lock = asyncio.Lock()

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            await self.asset_store.ensure_indexes()
            await self.sitemap_store.ensure_indexes()
            await self.report_store.ensure_indexes()

            self._initialized = True