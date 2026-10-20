from __future__ import annotations

import logging

from src.base.operations.models import OperationRecord
from src.base.operations.policies import job_policy
from src.base.operations.runner import (
    OperationAlreadyFinishedError,
    OperationNotRunningError,
)

logger = logging.getLogger(__name__)


@job_policy(producer="web_crawler")
class WebCrawlerJobPolicy:
    def __init__(
        self,
        *,
        cleanup_service,
        operation_service,
        operation_runner,
    ) -> None:
        self.cleanup_service = cleanup_service
        self.operation_service = operation_service
        self.operation_runner = operation_runner

    async def cleanup(
        self,
        operation: OperationRecord,
    ) -> None:
        await self.cleanup_service.finalize_run(
            run_id=operation.run_id,
            status=operation.status,
        )

    async def handle_stale(
        self,
        operation: OperationRecord,
    ) -> None:
        run_id = operation.run_id

        logger.warning(
            "Handling stale crawler operation: run_id=%s",
            run_id,
        )

        try:
            await self.operation_runner.cancel(run_id)

        except OperationAlreadyFinishedError:
            # The normal terminal-state inspection will perform cleanup.
            return

        except OperationNotRunningError:
            await self.operation_service.fail(
                run_id,
                error={
                    "type": "stale_operation",
                    "message": (
                        "Crawler operation became stale and had "
                        "no active runtime task."
                    ),
                },
            )

        # Cancellation is asynchronous. Do not clean resources until the
        # crawler has stopped using them.
        await self.cleanup_service.wait_until_run_idle(
            run_id=run_id,
            timeout_seconds=30,
        )

        await self.cleanup_service.abort_run(
            run_id=run_id,
        )