from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from src.domains.agentverse.tools.web_crawler_tool.persistence.assets.crawler_asset_repository import (
    CrawlerAssetRepository,
)
from src.domains.agentverse.tools.web_crawler_tool.persistence.reports.crawler_report_repository import (
    CrawlReportRepository,
)
from src.domains.agentverse.tools.web_crawler_tool.persistence.sitemap.sitemap_url_inventory_repository import (
    SitemapUrlInventoryRepository,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import (
        MongoDBRepository,
    )

class CrawlerDBService:
    def __init__(
        self,
        repository: MongoDBRepository,
    ) -> None:
        self.assets = CrawlerAssetRepository(repository)
        self.sitemap = SitemapUrlInventoryRepository(repository)
        self.reports = CrawlReportRepository(repository)

    async def ensure_asset_indexes(self) -> None:
        return await self.assets.ensure_asset_indexes()

    async def observe_assets(self, **kwargs: Any) -> dict[str, int]:
        return await self.assets.observe_assets(**kwargs)
    
    async def observe_asset(
        self,
        **kwargs: Any,
    ) -> bool:
        return await self.assets.observe_asset(**kwargs)

    async def get_asset_run_summary(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await self.assets.get_asset_run_summary(**kwargs)
    
    def iter_asset_run_observations(
        self,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        return self.assets.iter_asset_run_observations(**kwargs)

    async def ensure_sitemap_url_inventory_indexes(self) -> None:
        return await self.sitemap.ensure_sitemap_url_inventory_indexes()

    async def get_leased_sitemap_urls_by_hashes(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return await self.sitemap.get_leased_sitemap_urls_by_hashes(
            **kwargs
        )
    
    async def add_sitemap_url_batch(
        self,
        **kwargs: Any,
    ) -> int:
        return await self.sitemap.add_sitemap_url_batch(
            **kwargs
        )
    
    async def complete_sitemap_url_batch(
        self,
        **kwargs: Any,
    ) -> dict[str, int]:
        return await self.sitemap.complete_sitemap_url_batch(
            **kwargs
        )
    
    def iter_sitemap_urls(
        self,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        return self.sitemap.iter_sitemap_urls(**kwargs)
    
    async def count_sitemap_urls(
        self,
        **kwargs: Any,
    ) -> int:
        return await self.sitemap.count_sitemap_urls(
            **kwargs
        )
    
    async def claim_sitemap_url_batch(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return await self.sitemap.claim_sitemap_url_batch(
            **kwargs
        )
    
    async def delete_sitemap_url_inventory(
        self,
        **kwargs: Any,
    ) -> int:
        return await self.sitemap.delete_sitemap_url_inventory(
            **kwargs
        )

    async def ensure_crawl_run_report_indexes(self) -> None:
        return await self.reports.ensure_crawl_run_report_indexes()
    
    async def get_crawl_run_report(
        self,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self.reports.get_crawl_run_report(**kwargs)
    
    async def set_crawl_run_report_metadata(
        self,
        **kwargs: Any,
    ) -> None:
        return await (
            self.reports
            .set_crawl_run_report_metadata(
                **kwargs
            )
        )

    async def apply_page_report_contribution(
        self,
        **kwargs: Any,
    ) -> bool:
        return await self.reports.apply_page_report_contribution(
            **kwargs
        )

    async def mark_crawl_run_report_complete(
        self,
        **kwargs: Any,
    ) -> None:
        return await self.reports.mark_crawl_run_report_complete(**kwargs)
    