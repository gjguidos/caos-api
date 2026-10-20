from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from src.base.operations.models import (
    OperationRecord,
    OperationStatus,
)
from src.base.operations.policies import registered_policies
from src.base.operations.service import OperationService

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
    }
)

ACTIVE_STATUSES = frozenset(
    {
        OperationStatus.PENDING,
        OperationStatus.RUNNING,
    }
)

JobHandler = Callable[
    [OperationRecord],
    Awaitable[None],
]

JobCleanupHandler = JobHandler
JobStaleHandler = JobHandler


@dataclass(slots=True)
class SupervisedJob:
    run_id: str
    producer: str
    registered_at: datetime
    last_seen_at: datetime
    last_status: OperationStatus | None = None
    last_progress_revision: int = -1
    cleanup_attempts: int = 0
    cleanup_handler: JobCleanupHandler | None = None
    stale_handler: JobStaleHandler | None = None
    stale_handled: bool = False


class JobSupervisor:
    """
    Global application-level supervisor for background operations.

    Tools register operations here. The supervisor:

    - observes operation state;
    - detects stale operations;
    - invokes stale-operation handlers;
    - invokes terminal cleanup handlers;
    - retries failed cleanup on later iterations.
    """

    def __init__(
        self,
        *,
        operation_service: OperationService,
        interval_seconds: float = 30.0,
        stale_after_seconds: float = 900.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                "interval_seconds must be greater than zero"
            )

        if stale_after_seconds <= 0:
            raise ValueError(
                "stale_after_seconds must be greater than zero"
            )

        self.operation_service = operation_service
        self.interval_seconds = interval_seconds
        self.stale_after_seconds = stale_after_seconds

        self._jobs: dict[str, SupervisedJob] = {}
        self._cleanup_handlers: dict[
            str,
            JobCleanupHandler,
        ] = {}
        self._stale_handlers: dict[
            str,
            JobStaleHandler,
        ] = {}

        self._processing: set[str] = set()
        self._missing_handler_warnings: set[tuple[str, str]] = set()

        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def register_policy(
        self,
        policy: object,
    ) -> None:
        producer = getattr(
            policy,
            "__job_producer__",
            None,
        )

        if not isinstance(producer, str) or not producer:
            raise ValueError(
                "Job policy must declare a producer."
            )

        cleanup = getattr(policy, "cleanup", None)

        if cleanup is None:
            raise ValueError(
                f"Job policy '{producer}' has no cleanup method."
            )

        self._cleanup_handlers[producer] = cleanup

        stale_handler = getattr(
            policy,
            "handle_stale",
            None,
        )

        if stale_handler is not None:
            self._stale_handlers[producer] = (
                stale_handler
            )

        self._missing_handler_warnings = {
            item
            for item in self._missing_handler_warnings
            if item[0] != producer
        }

    def _load_registered_policies(self) -> None:
        """Instantiate and register all imported decorated policies."""
        for definition in registered_policies().values():
            if definition.producer in self._cleanup_handlers:
                continue

            logger.info(
                "Job policy loaded: producer=%s policy=%s",
                definition.producer,
                definition.policy_type.__name__,
            )

    def list_producers(self) -> list[str]:
        """Return producers with currently loaded policies."""
        return sorted(self._cleanup_handlers)

    def register_cleanup_handler(
        self,
        *,
        producer: str,
        handler: JobCleanupHandler,
    ) -> None:
        self._cleanup_handlers[producer] = handler

        self._missing_handler_warnings = {
            item
            for item in self._missing_handler_warnings
            if item[0] != producer
        }

    def unregister_cleanup_handler(
        self,
        *,
        producer: str,
    ) -> None:
        self._cleanup_handlers.pop(producer, None)

    def register_stale_handler(
        self,
        *,
        producer: str,
        handler: JobStaleHandler,
    ) -> None:
        self._stale_handlers[producer] = handler

    def unregister_stale_handler(
        self,
        *,
        producer: str,
    ) -> None:
        self._stale_handlers.pop(producer, None)

    async def register_job(
        self,
        *,
        run_id: str,
        producer: str,
        policy: object | None = None,
    ) -> None:
        cleanup = getattr(policy, "cleanup", None)
        stale = getattr(policy, "handle_stale", None)

        policy_producer = getattr(
            policy,
            "__job_producer__",
            None,
        )

        if policy is not None and policy_producer != producer:
            raise ValueError(
                "Job policy producer mismatch: "
                f"expected={producer} actual={policy_producer!r}"
            )

        now = datetime.now(UTC)

        async with self._lock:
            self._jobs[run_id] = SupervisedJob(
                run_id=run_id,
                producer=producer,
                registered_at=now,
                last_seen_at=now,
                cleanup_handler=cleanup,
                stale_handler=stale,
            )

        logger.debug(
            "Job registered: producer=%s run_id=%s",
            producer,
            run_id,
        )

    async def unregister_job(
        self,
        *,
        run_id: str,
    ) -> None:
        async with self._lock:
            job = self._jobs.pop(run_id, None)
            self._processing.discard(run_id)

        if job is not None:
            logger.debug(
                "Job unregistered: producer=%s run_id=%s",
                job.producer,
                run_id,
            )

    async def start(self) -> None:
        if (
            self._task is not None
            and not self._task.done()
        ):
            return

        self._load_registered_policies()

        self._stop_event.clear()

        self._task = asyncio.create_task(
            self._run(),
            name="job-supervisor",
        )

        logger.info(
            "Job supervisor started: producers=%s",
            self.list_producers(),
        )

    async def close(self) -> None:
        self._stop_event.set()

        task = self._task

        if task is not None:
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

        self._task = None

        async with self._lock:
            self._jobs.clear()
            self._processing.clear()

        logger.info("Job supervisor stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.inspect_once()

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Job supervisor inspection failed"
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )

            except TimeoutError:
                pass

    async def inspect_once(self) -> None:
        async with self._lock:
            jobs = list(self._jobs.values())

        for job in jobs:
            await self._inspect_job(job)

    async def _inspect_job(
        self,
        job: SupervisedJob,
    ) -> None:
        async with self._lock:
            if job.run_id in self._processing:
                return

            self._processing.add(job.run_id)
            
        try:
            operation = await self.operation_service.get(
                job.run_id
            )

            if operation is None:
                # Keep supervising it. Removing it here could leave
                # run-scoped crawler data without cleanup.
                logger.warning(
                    "Supervised operation unavailable; "
                    "retaining job: run_id=%s producer=%s",
                    job.run_id,
                    job.producer,
                )
                return

            await self._record_observation(
                job=job,
                operation=operation,
            )

            if operation.status in ACTIVE_STATUSES:
                await self._inspect_active_job(
                    job=job,
                    operation=operation,
                )
                return

            if operation.status in TERMINAL_STATUSES:
                await self._cleanup_terminal_job(
                    job=job,
                    operation=operation,
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Job inspection failed: "
                "producer=%s run_id=%s",
                job.producer,
                job.run_id,
            )

        finally:
            async with self._lock:
                self._processing.discard(job.run_id)

    async def _record_observation(
        self,
        *,
        job: SupervisedJob,
        operation: OperationRecord,
    ) -> None:
        state_changed = (
            job.last_status != operation.status
            or job.last_progress_revision
            != operation.progress_revision
        )

        if not state_changed:
            return

        async with self._lock:
            current = self._jobs.get(job.run_id)

            if current is None:
                return

            current.last_status = operation.status
            current.last_progress_revision = (
                operation.progress_revision
            )
            current.last_seen_at = datetime.now(UTC)

    async def _inspect_active_job(
        self,
        *,
        job: SupervisedJob,
        operation: OperationRecord,
    ) -> None:
        now = datetime.now(UTC)
        age_seconds = (
            now - job.last_seen_at
        ).total_seconds()

        if age_seconds < self.stale_after_seconds:
            return

        if job.stale_handled:
            return

        handler = job.stale_handler

        if handler is None:
            logger.warning(
                "Stale job detected but no stale handler exists: "
                "producer=%s run_id=%s age_seconds=%.1f",
                job.producer,
                job.run_id,
                age_seconds,
            )
            return

        logger.warning(
            "Handling stale job: producer=%s run_id=%s "
            "age_seconds=%.1f",
            job.producer,
            job.run_id,
            age_seconds,
        )

        await handler(operation)

        async with self._lock:
            current = self._jobs.get(job.run_id)

            if current is not None:
                current.stale_handled = True

    async def _cleanup_terminal_job(
        self,
        *,
        job: SupervisedJob,
        operation: OperationRecord,
    ) -> None:
        handler = job.cleanup_handler

        if handler is None:
            warning_key = (
                job.producer,
                job.run_id,
            )

            if warning_key not in self._missing_handler_warnings:
                logger.warning(
                    "Terminal job has no cleanup handler: "
                    "producer=%s run_id=%s",
                    job.producer,
                    job.run_id,
                )
                self._missing_handler_warnings.add(
                    warning_key
                )

            return

        async with self._lock:
            current = self._jobs.get(job.run_id)

            if current is not None:
                current.cleanup_attempts += 1
                attempt = current.cleanup_attempts
            else:
                attempt = 1

        try:
            await handler(operation)

        except Exception:
            logger.exception(
                "Job cleanup failed: producer=%s run_id=%s "
                "attempt=%s",
                job.producer,
                job.run_id,
                attempt,
            )
            return

        logger.info(
            "Supervised operation cleaned: "
            "producer=%s run_id=%s status=%s attempt=%s",
            job.producer,
            job.run_id,
            operation.status.value,
            attempt,
        )

        await self.unregister_job(
            run_id=job.run_id,
        )
