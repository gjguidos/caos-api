from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from .cpu_executor_registry import (
    CpuExecutor,
    cpu_executor_registry,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=CpuExecutor)

def register_cpu_executor(
    factory: Callable[..., T],
) -> Callable[..., T]:
    @wraps(factory)
    def wrapper(
        *args: Any,
        **kwargs: Any,
    ) -> T:
        executor = factory(
            *args,
            **kwargs,
        )

        cpu_executor_registry.register(executor)
        logger.info(
            "[📏REGISTER][🖥️CPU_EXECUTOR] ",
            "type=%s id=%s total=%s",
            type(executor).__name__,
            id(executor),
            cpu_executor_registry.count(),
        )
        return executor

    return wrapper