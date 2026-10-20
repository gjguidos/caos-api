from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from src.domains.agentverse.tools.web_crawler_tool.acquisition.models import CrawlAcquisitionResult
from src.domains.agentverse.tools.web_crawler_tool.acquisition.progress import (
    report_acquisition_progress,
)
from src.domains.agentverse.tools.web_crawler_tool.config import WebCrawlerToolConfig
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    execute_crawl_chunk,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlChunk,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import ResolvedCrawlerRuntime
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)


async def sequential_sitemap_crawl(
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: Any,
    agent: Any,
    sitemap_url_inventory_store: Any,
    operation_service: Any,
    planned_total: int,
) -> CrawlAcquisitionResult:
    total_planned = 0
    total_acquired = 0
    total_successful = 0
    total_failed = 0
    chunks_completed = 0
    chunk_index = 0

    while total_planned < planned_total and (runtime.page_acquisition_limit is None or total_planned < runtime.page_acquisition_limit):
        chunk_id = str(uuid4())
        lease_id = chunk_id
        lease_until = datetime.now(UTC) + timedelta(seconds=300)
        remaining_limit = None if runtime.page_acquisition_limit is None else runtime.page_acquisition_limit - total_planned
        batch_size = cfg.crawl_plan_chunk_size

        if remaining_limit is not None:
            batch_size = min(batch_size, remaining_limit)

        records = await sitemap_url_inventory_store.claim_batch(
            run_id=run_id,
            lease_id=lease_id,
            batch_size=batch_size,
            lease_until=lease_until,
        )

        if not records:
            break

        chunk = CrawlChunk(
            index=chunk_index,
            targets=[
                CrawlTarget(
                    url=record.url,
                    source="sitemap",
                    depth=0,
                    metadata={
                        "url_hash": record.url_hash,
                        "source_sitemap_url": record.source_sitemap_url,
                        "discovery_order": record.discovery_order,
                    },
                )
                for record in records
            ],
        )

        chunk_result = await execute_crawl_chunk(
            run_id=run_id,
            cfg=cfg,
            chunk=chunk,
            runtime=runtime,
            start_url=start_url,
            crawl_mode=crawl_mode,
            fetcher=fetcher,
            auth_context=auth_context,
            agent=agent,
            rate_limit_delay_ms=cfg.rate_limit_delay_ms,
            security=cfg.security,
            on_progress=report_acquisition_progress,
        )

        total_planned += chunk_result.planned
        total_acquired += chunk_result.fetched
        total_successful += chunk_result.queued_for_parse
        total_failed += chunk_result.failed
        chunks_completed += 1
        chunk_index += 1

    return CrawlAcquisitionResult(
        planned=total_planned,
        fetched=total_acquired,
        queued_for_parse=total_successful,
        failed=total_failed,
        chunks_completed=chunks_completed,
    )