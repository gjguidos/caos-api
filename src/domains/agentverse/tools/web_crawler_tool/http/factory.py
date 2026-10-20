from __future__ import annotations

from typing import TYPE_CHECKING

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DEFAULT_USER_AGENT,
)

from .crawler_http_client import CrawlerHttpClient

if TYPE_CHECKING:
    from src.domains.agentverse.tools.web_crawler_tool.config import (
        WebCrawlerToolConfig,
    )


def build_crawler_http_client(
    *,
    cfg: WebCrawlerToolConfig,
    timeout_seconds: int,
    max_connections: int | None = None,
) -> CrawlerHttpClient:
    connections = (
        max_connections
        if max_connections is not None
        else cfg.page_acquisition_concurrency
    )

    return CrawlerHttpClient(
        timeout_seconds=timeout_seconds,
        user_agent=(
            cfg.user_agent
            or DEFAULT_USER_AGENT
        ),
        max_connections=connections,
        max_keepalive_connections=connections,
    )
