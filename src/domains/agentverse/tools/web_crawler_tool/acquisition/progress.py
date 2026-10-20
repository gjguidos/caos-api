from __future__ import annotations

from time import perf_counter
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
)


# USE FOR NORMAL CRAWL PROGRESS REPORTING
async def report_acquisition_progress(
    run_id: str,
    progress: CrawlAcquisitionProgress,
    is_sitemap_chunked: bool,
    total_acquired: int,
    total_successful: int,
    total_failed: int,
    acquisition_started: float,
    planned_total: int,
    chunks_completed: int,
    total_chunks: int,
    active_chunk_index: int | None,
    active_chunk_planned: int,
    started_perf: float,
    runtime: ResolvedCrawlerRuntime,
    operation_service: Any,

) -> None:
    if is_sitemap_chunked:
        global_acquired = total_acquired + progress.current
        global_successful = (
            total_successful + progress.successful
        )
        global_failed = total_failed + progress.failed
        global_elapsed_seconds = max(
            0.0,
            perf_counter() - acquisition_started,
        )
        pages_per_second = (
            global_acquired / global_elapsed_seconds
            if global_elapsed_seconds > 0
            else 0.0
        )
        remaining_to_limit = max(
            planned_total - global_acquired,
            0,
        )
        eta_to_limit_seconds = (
            remaining_to_limit / pages_per_second
            if pages_per_second > 0
            else None
        )
        elapsed_seconds = round(
            pages_per_second,
            3
        )
        eta_seconds = (
            round(eta_to_limit_seconds, 3)
            if eta_to_limit_seconds is not None
            else None
        )
        eta_value = (
            round(eta_seconds, 3)
            if eta_seconds is not None
            else None
        )
        metadata = {
            "scope": "sitemap",
            "global": {
                "planned": planned_total,
                "acquired": global_acquired,
                "successful": global_successful,
                "failed": global_failed,
                "chunks_completed": chunks_completed,
                "total_chunks": total_chunks,
                "elapsed_seconds": round(
                    global_elapsed_seconds,
                    3,
                ),
                "pages_per_second": pages_per_second,
                "eta_seconds": eta_seconds,
                "eta_value": eta_value,
            },
            "chunk": {
                "index": active_chunk_index,
                "planned": active_chunk_planned,
                "acquired": progress.current,
                "successful": progress.successful,
                "failed": progress.failed,
                "current_url": progress.current_url,
                "depth": progress.depth,
                "queued": progress.queued,
                "visited": progress.visited,
            },
            "pages_per_second": round(
                pages_per_second,
                3,
            ),
            "eta_to_limit_seconds": (
                round(eta_to_limit_seconds, 3)
                if eta_to_limit_seconds is not None
                else None
            ),
        }
        current = global_acquired
        total = planned_total
        message = "Crawling sitemap pages."
    else:
        elapsed_seconds = max(
            0.0,
            perf_counter() - started_perf,
        )
        pages_per_second = (
            progress.current / elapsed_seconds
            if elapsed_seconds > 0
            else 0.0
        )
        remaining_to_limit = max(
            progress.limit - progress.current,
            0,
        )
        eta_to_limit_seconds = (
            remaining_to_limit / pages_per_second
            if pages_per_second > 0
            else None
        )
        metadata = {
            "scope": "normal",
            "page_limit": runtime.max_pages,
            "limit_is_exact": False,
            "pages_per_second": round(
                pages_per_second,
                3,
            ),
            "eta_to_limit_seconds": (
                round(eta_to_limit_seconds, 3)
                if eta_to_limit_seconds is not None
                else None
            ),
        }
        current = progress.current
        total = progress.limit
        message = "Acquiring pages."
    await operation_service.update_progress(
        run_id,
        phase="page_acquisition",
        current=current,
        total=total,
        message=message,
        metadata=metadata,
    )

async def report_queued_chunk_complete(
    run_id: str,
    acquisition_started: float,
    planned_total: int,
    chunk_result: CrawlChunkResult,
    total_chunks: int,
    completed_chunks: int,
    completed_planned: int,
    completed_acquired: int,
    completed_successful: int,
    completed_failed: int,
    operation_service: Any,
) -> None:
    elapsed_seconds = max(
        0.0,
        perf_counter() - acquisition_started,
    )
    pages_per_second = (
        completed_acquired / elapsed_seconds
        if elapsed_seconds > 0
        else 0.0
    )
    remaining = max(
        planned_total - completed_acquired,
        0,
    )
    eta_seconds = (
        remaining / pages_per_second
        if pages_per_second > 0
        else None
    )
    eta_value = (
        round(eta_seconds, 3)
        if eta_seconds is not None
        else None
    )
    await operation_service.update_progress(
        run_id,
        phase="page_acquisition",
        current=completed_acquired,
        total=planned_total,
        message="Crawling sitemap pages.",
        metadata={
            "scope": "sitemap",
            "global": {
                "planned": planned_total,
                "acquired": completed_acquired,
                "successful": completed_successful,
                "failed": completed_failed,
                "chunks_completed": completed_chunks,
                "total_chunks": total_chunks,
                "queued_chunks": max(
                    0,
                    total_chunks - completed_chunks - 1,
                ),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "pages_per_second": round(pages_per_second, 3),
                "eta_seconds": eta_value,
            },
            "pages_per_second": round(pages_per_second, 3),
            "eta_to_limit_seconds": eta_value,
            "chunk": {
                "index": chunk_result.chunk_index,
                "planned": chunk_result.planned,
                "fetched": chunk_result.fetched,
                "queued_for_parse": (
                    chunk_result.queued_for_parse
                ),
                "failed": chunk_result.failed,
            },
        },
    )

async def report_queued_chunk_progress(
    run_id: str,
    acquisition_started: float,
    accumulator: Any,
    progress: CrawlAcquisitionProgress,
    planned_total: int,
    chunk_index: int,
    completed_chunks: int,
    completed_planned: int,
    completed_acquired: int,
    completed_successful: int,
    completed_failed: int,
    operation_service: Any,
) -> None:
    totals = await accumulator.update_chunk_progress(
        chunk_index=chunk_index,
        planned=progress.limit,
        fetched=progress.current,
        queued_for_parse=progress.successful,
        failed=progress.failed,
    )
    
    global_acquired = totals["fetched"]
    global_successful = totals["queued_for_parse"]
    global_failed = totals["failed"]
    elapsed_seconds = max(
        0.0,
        perf_counter() - acquisition_started,
    )
    pages_per_second = (
        global_acquired / elapsed_seconds
        if elapsed_seconds > 0
        else 0.0
    )
    remaining = max(
        planned_total - global_acquired,
        0,
    )
    eta_seconds = (
        remaining / pages_per_second
        if pages_per_second > 0
        else None
    )
    eta_value = (
        round(eta_seconds, 3)
        if eta_seconds is not None
        else None
    )

    await operation_service.update_progress(
        run_id,
        phase="page_acquisition",
        current=global_acquired,
        total=planned_total,
        message="Crawling sitemap pages.",
        metadata={
            "scope": "sitemap",
            "global": {
                "planned": planned_total,
                "fetched": global_acquired,
                "queued_for_parse": global_successful,
                "failed": global_failed,
                "elapsed_seconds": round(
                    elapsed_seconds,
                    3,
                ),
                "pages_per_second": round(
                    pages_per_second,
                    3,
                ),
                "eta_seconds": eta_value,
            },
            "chunk": {
                "index": chunk_index,
                "planned": progress.limit,
                "fetched": progress.current,
                "queued_for_parse": progress.successful,
                "failed": progress.failed,
                "current_url": progress.current_url,
            },
        },
    )