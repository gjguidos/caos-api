from __future__ import annotations

from typing import Protocol

from src.base.queues.models import AgentVerseJob


class JobQueue(Protocol):
    async def ensure_group(
        self,
        *,
        queue_name: str,
        consumer_group: str,
    ) -> None:
        ...

    async def publish(
        self,
        *,
        queue_name: str,
        job: AgentVerseJob,
    ) -> str:
        ...

    async def consume(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> list[tuple[str, AgentVerseJob]]:
        ...

    async def acknowledge(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        message_id: str,
    ) -> None:
        ...

    async def reclaim_stale(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[tuple[str, AgentVerseJob]]:
        ...