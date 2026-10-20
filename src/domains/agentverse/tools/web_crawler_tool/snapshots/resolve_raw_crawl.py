from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.agent_interface import (
    AgentInterface,
)
from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerSecurityConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.engine.crawler import (
    CrawlEngine,
    CrawlProgressCallback,
    CrawlResult,
    PageFetchedCallback,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)


async def resolve_raw_crawl(
    *,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    fetcher: Any,
    auth_context: CrawlAuthContext | None,
    agent: AgentInterface | None,
    rate_limit_delay_ms: int,
    security: WebCrawlerSecurityConfig,
    target_urls: list[str],
    on_page_fetched: PageFetchedCallback,
    on_progress: CrawlProgressCallback | None = None,
    acquisition_concurrency: int | None = None,
    discover_links: bool = False,
) -> CrawlResult:
    if not target_urls:
        raise ValueError(
            "Raw crawl requires target_urls."
        )

    results = await CrawlEngine().crawl(
        agent=agent,
        start_url=start_url,
        target_urls=target_urls,
        mode="static",
        fetcher=fetcher,
        auth_context=auth_context,
        acquisition_concurrency=(
            acquisition_concurrency or 4
        ),
        discover_links=discover_links,
        max_pages=runtime.max_pages,
        max_depth=runtime.max_depth,
        crawl_strategy=runtime.crawl_strategy,
        same_domain_only=runtime.same_domain_only,
        include_links=runtime.include_links,
        rate_limit_delay_ms=rate_limit_delay_ms,
        security=security,
        context=runtime.params.context,
        on_progress=on_progress,
        on_page_fetched=on_page_fetched,
    )

    return results
