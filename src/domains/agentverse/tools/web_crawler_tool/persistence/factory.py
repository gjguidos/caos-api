from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.persistence.crawler_db_service import (
    CrawlerDBService,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import (
        MongoDBRepository,
    )


def build_crawler_db_service(
    *,
    repository: MongoDBRepository | None,
) -> CrawlerDBService | None:
    if repository is None:
        return None

    return CrawlerDBService(
        repository=repository,
    )