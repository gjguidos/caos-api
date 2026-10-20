from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.scope import (
    CrawlScopeResult,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.site_scope import (
    execute_site_scope,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.sitemap_scope import (
    execute_sitemap_scope,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    PreparedCrawlExecution,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)


async def resolve_crawl_scope(
    *,
    agent: Any,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    site_state_store: Any,
    sitemap_url_inventory_store: Any,
    operation_service: Any,
    started_at: datetime,
    started_perf: float,
    tool_key: str,
    tool_version: str,
    format_result: Callable[[Any], Any] | None = None,
) -> CrawlScopeResult:
    if runtime.crawl_scope == "sitemap":
        return await execute_sitemap_scope(
            agent=agent,
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            prepared=prepared,
            operation_service=operation_service,
            sitemap_url_inventory_store=sitemap_url_inventory_store,
            started_at=started_at,
            started_perf=started_perf,
            tool_key=tool_key,
            tool_version=tool_version,
            format_result=format_result,
        )

    if runtime.crawl_scope == "site":
        return await execute_site_scope(
            cfg=cfg,
            runtime=runtime,
            prepared=prepared,
            site_state_store=site_state_store,
            operation_service=operation_service,
            run_id=run_id,
        )

    raise ValueError(
        f"Unsupported crawl scope: {runtime.crawl_scope}"
    )