from __future__ import annotations

import httpx

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DEFAULT_USER_AGENT,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.browser_fetcher import (
    BrowserPageFetcher,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.browser_session_fetcher import (
    BrowserSessionPageFetcher,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.static_fetcher import (
    StaticPageFetcher,
)


def build_page_fetcher(
    *,
    cfg: WebCrawlerToolConfig,
    mode: str,
    auth_context: CrawlAuthContext | None,
    timeout_seconds: int,
    max_chars_per_page: int,
    http_client: httpx.AsyncClient | None = None,
):
    """Build the fetcher for the selected crawler mode.

    Static fetching now returns raw ``FetchedPage`` objects. Browser
    fetchers still return their existing parsed-page contract and therefore
    must not be passed through the static parser-worker pipeline yet.
    """
    if mode == "static":
        return StaticPageFetcher(
            timeout_seconds=timeout_seconds,
            user_agent=cfg.user_agent or DEFAULT_USER_AGENT,
            allowed_content_types=cfg.security.allowed_content_types,
            max_response_bytes=cfg.security.max_response_bytes,
            security=cfg.security,
            client=http_client,
        )

    if mode == "browser":
        if (
            auth_context
            and auth_context.mode == "browser_context"
        ):
            return BrowserSessionPageFetcher(
                timeout_seconds=timeout_seconds,
                max_chars_per_page=max_chars_per_page,
                max_response_bytes=cfg.security.max_response_bytes,
                wait_until=cfg.browser_wait_until,
                wait_ms=cfg.browser_wait_ms,
            )

        return BrowserPageFetcher(
            timeout_seconds=timeout_seconds,
            user_agent=cfg.user_agent or DEFAULT_USER_AGENT,
            max_chars_per_page=max_chars_per_page,
            allowed_content_types=cfg.security.allowed_content_types,
            max_response_bytes=cfg.security.max_response_bytes,
            security=cfg.security,
            wait_until=cfg.browser_wait_until,
            wait_ms=cfg.browser_wait_ms,
            viewport_width=cfg.browser_viewport_width,
            viewport_height=cfg.browser_viewport_height,
            block_resources=cfg.browser_block_resources,
            allowed_resource_types=cfg.browser_allowed_resource_types,
        )

    if mode == "auto":
        raise ToolExecutionError(
            "Auto mode is not available in the raw-page pipeline yet. "
            "Migrate AutoFallbackFetcher to the FetchedPage contract "
            "before enabling it here."
        )

    raise ToolExecutionError(
        f"Unsupported crawler mode: {mode}"
    )
