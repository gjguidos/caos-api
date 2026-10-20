from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.scope import (
    CrawlScopeResult,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    PreparedCrawlExecution,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.resolver import (
    resolve_requested_site_coverage,
)


async def execute_site_scope(
    *,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    site_state_store: Any,
    operation_service: Any,
    run_id: str,
) -> CrawlScopeResult:
    await operation_service.update_progress(
        run_id,
        phase="coverage",
        current=0,
        message="Resolving existing site coverage.",
    )

    coverage = await resolve_requested_site_coverage(
        store=site_state_store,
        start_url=prepared.start_url,
        max_depth=runtime.max_depth,
        freshness_ttl_seconds=cfg.page_freshness_ttl_seconds,
    )
    await operation_service.update_progress(
        run_id,
        phase="planning",
        current=0,
        message="Starting a live crawl from the requested site URL.",
    )

    return CrawlScopeResult(
        crawl_plan=None,
        coverage=coverage,
        sitemap_plan=None,
        metadata={
            "scope": "site",
        },
    )
