from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
)


@dataclass
class ActiveChunkProgress:
    planned: int = 0
    fetched: int = 0
    queued_for_parse: int = 0
    failed: int = 0


@dataclass
class CrawlRunAccumulator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        self._results: dict[int, CrawlChunkResult] = {}
        self._active_chunks: dict[
            int,
            ActiveChunkProgress,
        ] = {}

        self._completed_planned = 0
        self._completed_fetched = 0
        self._completed_queued_for_parse = 0
        self._completed_failed = 0

        self.errors: list[str] = []
        self.report_totals: dict[str, Any] = {}

    async def update_chunk_progress(
        self,
        *,
        chunk_index: int,
        planned: int,
        fetched: int,
        queued_for_parse: int,
        failed: int,
    ) -> dict[str, int]:
        async with self._lock:
            self._active_chunks[chunk_index] = (
                ActiveChunkProgress(
                    planned=planned,
                    fetched=fetched,
                    queued_for_parse=queued_for_parse,
                    failed=failed,
                )
            )

            return self._totals_unlocked()

    async def record(
        self,
        result: CrawlChunkResult,
    ) -> None:
        async with self._lock:
            chunk_index = result.chunk_index

            self._active_chunks.pop(
                chunk_index,
                None,
            )

            previous = self._results.get(chunk_index)

            if previous is not None:
                self._completed_planned -= previous.planned
                self._completed_fetched -= previous.fetched
                self._completed_queued_for_parse -= (
                    previous.queued_for_parse
                )
                self._completed_failed -= previous.failed

            self._results[chunk_index] = result

            self._completed_planned += result.planned
            self._completed_fetched += result.fetched
            self._completed_queued_for_parse += (
                result.queued_for_parse
            )
            self._completed_failed += result.failed

    async def results(self) -> list[CrawlChunkResult]:
        async with self._lock:
            return [
                self._results[index]
                for index in sorted(self._results)
            ]

    async def completed_chunks(self) -> int:
        async with self._lock:
            return len(self._results)

    async def totals(self) -> dict[str, int]:
        async with self._lock:
            return self._totals_unlocked()

    def _totals_unlocked(self) -> dict[str, int]:
        active_planned = 0
        active_fetched = 0
        active_queued_for_parse = 0
        active_failed = 0

        for progress in self._active_chunks.values():
            active_planned += progress.planned
            active_fetched += progress.fetched
            active_queued_for_parse += (
                progress.queued_for_parse
            )
            active_failed += progress.failed

        return {
            "planned": (
                self._completed_planned
                + active_planned
            ),
            "fetched": (
                self._completed_fetched
                + active_fetched
            ),
            "queued_for_parse": (
                self._completed_queued_for_parse
                + active_queued_for_parse
            ),
            "failed": (
                self._completed_failed
                + active_failed
            ),
        }