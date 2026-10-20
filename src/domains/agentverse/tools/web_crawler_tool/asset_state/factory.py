from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)

if TYPE_CHECKING:
    from src.domains.agentverse.tools.web_crawler_tool.persistence.crawler_db_service import (
        CrawlerDBService,
    )


def build_asset_state_store(
    *,
    crawler_db_service: CrawlerDBService,
) -> AssetStateStore:
    return AssetStateStore(
        db_service=crawler_db_service,
    )