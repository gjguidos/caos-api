from __future__ import annotations

import asyncio

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


def log_report_task_done(
    run_id: str,
    task: asyncio.Task,
) -> None:
    if task.cancelled():
        AGENTVERSE_LOGGER.exception(
            LogDomain.TOOL_BAY,
            f"Crawler report worker pool was cancelled: run_id={run_id}",
        )
        return
    exception = task.exception()
    if exception is not None:
        AGENTVERSE_LOGGER.exception(
            LogDomain.TOOL_BAY,
            (
                "Crawler report worker pool exited with an error: "
                f"run_id={run_id} type={type(exception).__name__} error={exception}",
            ),
        )
    else:
        AGENTVERSE_LOGGER.exception(
            LogDomain.TOOL_BAY,
            f"Crawler report worker pool exited unexpectedly: run_id={run_id}",
        )