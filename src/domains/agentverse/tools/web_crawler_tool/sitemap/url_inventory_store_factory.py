# sitemap/url_inventory_store_factory.py

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)

if TYPE_CHECKING:
    from src.domains.agentverse.tools.web_crawler_tool.persistence.crawler_db_service import (
        CrawlerDBService,
    )


def build_sitemap_url_inventory_store(
    *,
    crawler_db_service: CrawlerDBService,
) -> SitemapUrlInventoryStore:
    return SitemapUrlInventoryStore(
        db_service=crawler_db_service,
    )