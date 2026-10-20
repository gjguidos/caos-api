# crawler_cpu_executor.py

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Any

from src.base.runtime.cpu_executor_register import (
    register_cpu_executor,
)


def _warmup_process() -> None:
    """Pickle-safe function used to start process workers."""
    return None

class CpuExecutor:
    def __init__(
        self,
        *,
        max_workers: int,
    ) -> None:
        self._max_workers = max_workers
        self._pool = ProcessPoolExecutor(
            max_workers=max_workers,
        )
        self._closed = False

    async def run(
        self,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if self._closed:
            raise RuntimeError(
                "CPU executor has been closed."
            )

        loop = asyncio.get_running_loop()

        if kwargs:
            function = partial(
                function,
                **kwargs,
            )

        return await loop.run_in_executor(
            self._pool,
            function,
            *args,
        )

    async def warmup(self) -> None:
        if self._closed:
            raise RuntimeError(
                "CPU executor has been closed."
            )

        await asyncio.gather(
            *(
                self.run(_warmup_process)
                for _ in range(self._max_workers)
            )
        )

    async def aclose(self) -> None:
        if self._closed:
            return

        self._closed = True

        await asyncio.to_thread(
            self._pool.shutdown,
            wait=True,
            cancel_futures=True,
        )


@register_cpu_executor
def create_cpu_executor(
    *,
    max_workers: int,
) -> CpuExecutor:
    return CpuExecutor(
        max_workers=max_workers,
    )


crawler_cpu_executor = create_cpu_executor(
    max_workers=4,
)

page_cpu_executor = create_cpu_executor(
    max_workers=6,
)