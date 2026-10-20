# execution/performance_observer.py

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Any


@dataclass
class StageStats:
    count: int = 0
    total_seconds: float = 0.0
    min_seconds: float = float("inf")
    max_seconds: float = 0.0

    def record(self, duration: float) -> None:
        self.count += 1
        self.total_seconds += duration
        self.min_seconds = min(self.min_seconds, duration)
        self.max_seconds = max(self.max_seconds, duration)

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total_seconds": round(self.total_seconds, 3),
            "average_seconds": round(
                self.total_seconds / self.count,
                3,
            ) if self.count else 0.0,
            "min_seconds": round(
                self.min_seconds,
                3,
            ) if self.count else 0.0,
            "max_seconds": round(
                self.max_seconds,
                3,
            ),
        }


class CrawlPerformanceObserver:
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        self._stages: dict[str, StageStats] = defaultdict(StageStats)
        self._counters: dict[str, int] = defaultdict(int)

    def increment(
        self,
        name: str,
        amount: int = 1,
    ) -> None:
        self._counters[name] += amount

    def record(
        self,
        stage: str,
        duration_seconds: float,
    ) -> None:
        self._stages[stage].record(duration_seconds)

    def timer(self, stage: str) -> PerformanceTimer:
        return PerformanceTimer(
            observer=self,
            stage=stage,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "counters": dict(self._counters),
            "stages": {
                name: stats.as_dict()
                for name, stats in self._stages.items()
            },
        }


class PerformanceTimer:
    def __init__(
        self,
        *,
        observer: CrawlPerformanceObserver,
        stage: str,
    ) -> None:
        self.observer = observer
        self.stage = stage
        self.started_at = 0.0

    async def __aenter__(self) -> PerformanceTimer:
        self.started_at = perf_counter()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.observer.record(
            self.stage,
            perf_counter() - self.started_at,
        )