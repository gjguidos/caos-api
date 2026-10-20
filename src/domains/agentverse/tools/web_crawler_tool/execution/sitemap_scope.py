from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.scope import (
    CrawlScopeResult,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.sitemap import (
    execute_sitemap_scope as _execute_sitemap_scope,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    PreparedCrawlExecution,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.progress import (
    SitemapDiscoveryProgress,
)


async def execute_sitemap_scope(
    *,
    agent: Any,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    operation_service: Any,
    sitemap_url_inventory_store: Any,
    started_at: datetime,
    started_perf: float,
    tool_key: str,
    tool_version: str,
    format_result: Callable[[Any], Any] | None = None,
) -> CrawlScopeResult:
    sitemap_progress_emit_interval_seconds = 0.5
    last_sitemap_progress_emit_at = 0.0

    async def report_sitemap_progress(
        progress: SitemapDiscoveryProgress,
    ) -> None:
        nonlocal last_sitemap_progress_emit_at

        total_sitemaps = (
            progress.processed_sitemaps
            + progress.queued_sitemaps
            + progress.active_sitemaps
        )

        now_perf = perf_counter()
        elapsed_seconds = max(
            0.0,
            now_perf - started_perf,
        )

        discovery_complete = (
            total_sitemaps > 0
            and progress.processed_sitemaps >= total_sitemaps
            and progress.queued_sitemaps == 0
            and progress.active_sitemaps == 0
        )

        first_emit = last_sitemap_progress_emit_at == 0.0
        interval_elapsed = (
            now_perf - last_sitemap_progress_emit_at
            >= sitemap_progress_emit_interval_seconds
        )

        if not (
            first_emit
            or interval_elapsed
            or discovery_complete
        ):
            return

        last_sitemap_progress_emit_at = now_perf

        await operation_service.update_progress(
            run_id,
            phase="sitemap_discovery",
            current=progress.processed_sitemaps,
            total=total_sitemaps,
            message=(
                "Sitemap targets discovered."
                if discovery_complete
                else "Discovering sitemap targets."
            ),
            metadata={
                "current_url": progress.current_url,
                "stage": progress.stage,
                "processed_sitemaps": progress.processed_sitemaps,
                "discovered_sitemaps": progress.discovered_sitemaps,
                "resolved_sitemaps": progress.resolved_sitemaps,
                "failed_sitemaps": progress.failed_sitemaps,
                "queued_sitemaps": progress.queued_sitemaps,
                "active_sitemaps": progress.active_sitemaps,
                "discovered_page_urls": progress.discovered_page_urls,
                "started_at": started_at.isoformat(),
                "elapsed_seconds": round(elapsed_seconds, 2),
                "resource_type": "sitemap",
                "limit_is_exact": False,
                "discovery_complete": discovery_complete,
            },
        )

    sitemap_plan = await _execute_sitemap_scope(
        run_id=run_id,
        cfg=cfg,
        runtime=runtime,
        prepared=prepared,
        agent=agent,
        sitemap_url_inventory_store=sitemap_url_inventory_store,
        on_progress=report_sitemap_progress,
    )

    return CrawlScopeResult(
        crawl_plan=sitemap_plan.crawl_plan,
        sitemap_plan=sitemap_plan,
        metadata={
            "scope": "sitemap",
            "planning_result": sitemap_plan.planning_result,
        },
    )