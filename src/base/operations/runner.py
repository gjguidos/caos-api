from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.base.operations.models import OperationStatus
from src.base.operations.service import OperationService

OperationCallable = Callable[
    [str],
    Awaitable[dict[str, Any]],
]

logger = logging.getLogger(__name__)

class OperationNotFoundError(Exception):
    pass


class OperationNotRunningError(Exception):
    pass


class OperationAlreadyFinishedError(Exception):
    pass


class OperationAlreadyRunningError(Exception):
    pass


class OperationRunner:
    def __init__(
        self,
        service: OperationService,
    ):
        self.service = service
        self._tasks: dict[
            str,
            asyncio.Task[None],
        ] = {}

    async def start(
        self,
        *,
        run_id: str,
        operation: OperationCallable,
    ) -> None:
        existing = self._tasks.get(
            run_id
        )

        if (
            existing is not None
            and not existing.done()
        ):
            raise OperationAlreadyRunningError(
                f"Operation '{run_id}' is already running."
            )

        task = asyncio.create_task(
            self._run(
                run_id=run_id,
                operation=operation,
            ),
            name=(
                f"agentverse-operation-{run_id}"
            ),
        )

        self._tasks[run_id] = task

        task.add_done_callback(
            lambda completed_task:
                self._remove_task(
                    run_id=run_id,
                    task=completed_task,
                )
        )

    async def cancel(
        self,
        run_id: str,
    ) -> None:
        operation = await self.service.get(
            run_id
        )

        if operation is None:
            raise OperationNotFoundError(
                f"Operation '{run_id}' was not found."
            )

        if operation.status in {
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        }:
            raise OperationAlreadyFinishedError(
                f"Operation '{run_id}' has already "
                f"finished with status "
                f"'{operation.status.value}'."
            )

        task = self._tasks.get(
            run_id
        )

        if task is None:
            raise OperationNotRunningError(
                f"Operation '{run_id}' exists but "
                "has no active runtime task."
        )

        if task.done():
            raise OperationAlreadyFinishedError(
                f"Operation '{run_id}' has already finished."
            )

        logger.info(
            "[⚙️ OPERATION]"
            "[CANCEL_REQUESTED] "
            f"run_id={run_id}"
        )

        task.cancel()

    async def _run(
        self,
        *,
        run_id: str,
        operation: OperationCallable,
    ) -> None:
        try:
            await self.service.mark_running(
                run_id
            )

            result = await operation(
                run_id
            )

        except asyncio.CancelledError:
            logger.info(
                "[⚙️ OPERATION]"
                "[CANCELLED] "
                f"run_id={run_id}"
            ),

            await self.service.cancel(
                run_id
            )

            raise

        except Exception as exc:
            log_exception_tree(
                exc,
                prefix=f"Operation failed run_id={run_id}: ",
            )

            await self.service.fail(
                run_id,
                error={
                    "type":
                        type(exc).__name__,
                    "message":
                        str(exc),
                },
            )

        else:
            await self.service.complete(
                run_id,
                result=result,
            )

    def _remove_task(
        self,
        *,
        run_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(
                run_id,
                None,
            )

def log_exception_tree(
    exc: BaseException,
    *,
    prefix: str = "",
) -> None:
    if isinstance(exc, BaseExceptionGroup):
        logger.error(
            "%sExceptionGroup: %d sub-exceptions",
            prefix,
            len(exc.exceptions),
        )

        for index, child in enumerate(exc.exceptions):
            log_exception_tree(
                child,
                prefix=f"{prefix}[{index}] ",
            )
        return

    logger.error(
        "%stype=%s error=%s",
        prefix,
        type(exc).__name__,
        exc,
        exc_info=exc,
    )