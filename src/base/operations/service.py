from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.base.events.models import DomainEvent
from src.base.events.publisher import DomainEventPublisher
from src.base.operations.events import (
    OperationEvent,
    OperationEventType,
)
from src.base.operations.models import (
    OperationProgress,
    OperationRecord,
    OperationStatus,
)
from src.base.operations.repository import OperationRepository

logger = logging.getLogger(__name__)


class OperationService:
    def __init__(
        self,
        repository: OperationRepository,
        event_publisher: DomainEventPublisher | None = None,
    ):
        self.repository = repository
        self.event_publisher = event_publisher

    async def create(
        self,
        *,
        kind: str,
        producer: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OperationRecord:
        operation = OperationRecord(
            run_id=uuid4().hex,
            kind=kind,
            producer=producer,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            metadata=metadata or {},
        )

        await self.repository.create(operation)

        return operation

    async def mark_running(
        self,
        run_id: str,
    ) -> OperationRecord:
        operation = await self._require(run_id)

        operation.status = OperationStatus.RUNNING
        operation.started_at = datetime.now(UTC)

        await self.repository.update(operation)

        await self._publish(
            OperationEventType.STARTED,
            operation,
        )

        return operation

    async def update_progress(
        self,
        run_id: str,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OperationRecord:
        operation = await self._require(run_id)
        previous = operation.progress

        progress = OperationProgress(
            phase=phase if phase is not None else previous.phase,
            current=current if current is not None else previous.current,
            total=total if total is not None else previous.total,
            message=message if message is not None else previous.message,
            metadata={
                **previous.metadata,
                **(metadata or {}),
            },
        )

        operation = await self.repository.update_progress(
            run_id,
            progress=progress,
        )

        progress_unchanged = (
            progress.phase == previous.phase
            and progress.current == previous.current
            and progress.total == previous.total
            and progress.message == previous.message
        )

        if progress_unchanged:
            return operation

        await self._publish(
            OperationEventType.PROGRESS,
            operation,
        )

        return operation
    
    async def list_operations(
        self,
        *,
        user_id: str | None = None,
        status: OperationStatus | None = None,
        limit: int = 50,
    ) -> list[OperationRecord]:
        return await self.repository.list(
            user_id=user_id,
            status=status,
            limit=limit,
        )

    async def complete(
        self,
        run_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> OperationRecord:
        operation = await self._require(run_id)

        operation.status = OperationStatus.COMPLETED
        operation.finished_at = datetime.now(UTC)
        operation.result = result

        await self.repository.update(operation)

        await self._publish(
            OperationEventType.COMPLETED,
            operation,
        )

        return operation

    async def fail(
        self,
        run_id: str,
        *,
        error: dict[str, Any],
    ) -> OperationRecord:
        operation = await self._require(run_id)

        operation.status = OperationStatus.FAILED
        operation.finished_at = datetime.now(UTC)
        operation.error = error

        await self.repository.update(operation)

        await self._publish(
            OperationEventType.FAILED,
            operation,
        )

        return operation
    
    async def get(
        self,
        run_id: str,
    ) -> OperationRecord | None:
        return await self.repository.get(run_id)

    async def cancel(
        self,
        run_id: str,
    ) -> OperationRecord:
        operation = await self._require(run_id)

        operation.status = OperationStatus.CANCELLED
        operation.finished_at = datetime.now(UTC)

        await self.repository.update(operation)

        await self._publish(
            OperationEventType.CANCELLED,
            operation,
        )

        return operation

    async def _require(
        self,
        run_id: str,
    ) -> OperationRecord:
        operation = await self.repository.get(run_id)

        if operation is None:
            raise KeyError(
                f"Operation '{run_id}' not found."
            )

        return operation

    async def _publish(
        self,
        event_type: OperationEventType,
        operation: OperationRecord,
    ) -> None:
        if self.event_publisher is None:
            return

        operation_event = OperationEvent(
            type=event_type,
            run_id=operation.run_id,
            kind=operation.kind,
            producer=operation.producer,
            status=operation.status,
            progress_revision=operation.progress_revision,
            message=self._resolve_event_message(
                operation,
                event_type,
            ),
            user_id=operation.user_id,
            agent_id=operation.agent_id,
            session_id=operation.session_id,
            progress=operation.progress,
            result=operation.result,
            error=operation.error,
        )

        try:
            await self.event_publisher.publish(
                DomainEvent(
                    event_type=operation_event.type.value,
                    source=operation.producer,
                    user_id=operation.user_id,
                    correlation_id=operation.run_id,
                    data=operation_event.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                )
            )

        except Exception:
            logger.exception(
                "Failed to publish operation event: "
                "event_type=%s run_id=%s producer=%s user_id=%s",
                event_type.value,
                operation.run_id,
                operation.producer,
                operation.user_id,
            )

    def _resolve_event_message(
        self,
        operation: OperationRecord,
        event_type: OperationEventType,
    ) -> str | None:
        event_messages = operation.metadata.get(
            "event_messages",
            {},
        )

        key = {
            OperationEventType.STARTED: "started",
            OperationEventType.COMPLETED: "completed",
            OperationEventType.FAILED: "failed",
            OperationEventType.CANCELLED: "cancelled",
        }.get(event_type)

        if key is None:
            return None

        if isinstance(event_messages, dict):
            message = event_messages.get(key)

            if (
                isinstance(message, str)
                and message.strip()
            ):
                return message

        defaults = {
            OperationEventType.STARTED:
                "Operation started.",
            OperationEventType.COMPLETED:
                "Operation completed successfully.",
            OperationEventType.FAILED:
                "Operation failed.",
            OperationEventType.CANCELLED:
                "Operation cancelled.",
        }

        return defaults.get(event_type)