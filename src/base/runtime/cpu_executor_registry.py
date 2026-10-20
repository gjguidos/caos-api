from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class CpuExecutor(Protocol):
    async def warmup(self) -> None:
        ...

    async def aclose(self) -> None:
        ...


class CpuExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[int, CpuExecutor] = {}

    def count(self) -> int:
        return len(self._executors)

    def snapshot(self) -> tuple[CpuExecutor, ...]:
        return tuple(self._executors.values())

    def register(self, executor: CpuExecutor) -> None:
        self._executors[id(executor)] = executor

        logger.info(
            "[📏REGISTRY][🖥️CPU_EXECUTOR] "
            "type=%s id=%s total=%s",
            type(executor).__name__,
            id(executor),
            len(self._executors),
        )

    def unregister(self, executor: CpuExecutor) -> None:
        self._executors.pop(id(executor), None)

    async def warmup_all(self) -> None:
        executors = self.snapshot()

        if not executors:
            return

        logger.info(
            "[📏REGISTRY][🔥CPU_EXECUTOR] warming_up count=%s",
            len(executors),
        )

        await asyncio.gather(
            *(executor.warmup() for executor in executors)
        )

        logger.info(
            "[📏REGISTRY][🔥CPU_EXECUTOR] warmup_complete count=%s",
            len(executors),
        )

    async def aclose_all(self) -> None:
        executors = self.snapshot()

        await asyncio.gather(
            *(executor.aclose() for executor in executors),
            return_exceptions=True,
        )

        self._executors.clear()


cpu_executor_registry = CpuExecutorRegistry()