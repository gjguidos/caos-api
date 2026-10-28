"""Infrastructure-independent snapshot storage contract and acceptance guards."""

from typing import Protocol
from uuid import UUID

from src.base.operations.v2.models import OperationRecord, OperationState
from src.base.operations.v2.transitions import (
    fail_before_start,
    finish_attempt,
    request_cancellation,
    start_attempt,
)


class OperationNotFound(LookupError):
    """No snapshot exists for this identity."""


class DuplicateOperation(ValueError):
    """Creation would overwrite an existing identity."""


class RevisionConflict(ValueError):
    """The expected predecessor revision is no longer current."""


class InvalidSnapshot(ValueError):
    """A snapshot is invalid or is not exactly one legal successor."""


class CorruptSnapshot(ValueError):
    """Stored data cannot be restored without repair."""


class OperationRepository(Protocol):
    """Async storage, not authorization or execution ownership.

    create accepts any validated snapshot, including imported terminal history.
    get raises OperationNotFound. commit requires exactly one legal transition
    and revision == expected_revision + 1; missing and stale writes never insert.
    """

    async def create(self, operation: OperationRecord) -> None: ...

    async def get(self, operation_id: UUID) -> OperationRecord: ...

    async def commit(self, operation: OperationRecord, *, expected_revision: int) -> None: ...


def validate_snapshot(operation: OperationRecord) -> OperationRecord:
    # Revalidation also catches trusted Pydantic bypasses and returns a detached value.
    try:
        return OperationRecord.model_validate(operation, strict=True)
    except ValueError as exc:
        raise InvalidSnapshot("Invalid operation snapshot") from exc


def validate_revision(operation: OperationRecord, expected_revision: int) -> None:
    if type(expected_revision) is not int or expected_revision < 0:
        raise InvalidSnapshot("Expected revision must be a nonnegative integer")
    if operation.revision != expected_revision + 1:
        raise InvalidSnapshot("Commit must advance exactly one revision")


def validate_successor(previous: OperationRecord, operation: OperationRecord) -> None:
    """Reuse OPS2-01 guards, rather than introducing a second transition model."""
    try:
        if operation.state == OperationState.RUNNING:
            candidate = start_attempt(
                previous, at=operation.updated_at, attempt_id=operation.attempts[-1].attempt_id
            )
        elif operation.cancellation_requested_at != previous.cancellation_requested_at:
            candidate = request_cancellation(
                previous, at=operation.updated_at, reason=operation.cancellation_reason or ""
            )
        elif previous.state == OperationState.PENDING:
            candidate = fail_before_start(
                previous, at=operation.updated_at, reason=operation.failure_reason or ""
            )
        else:
            last = operation.attempts[-1]
            candidate = finish_attempt(
                previous,
                attempt_id=last.attempt_id,
                outcome=last.state,
                at=operation.updated_at,
                reason=last.reason,
                retry=operation.state == OperationState.RETRY_PENDING,
            )
        if candidate != operation:
            raise InvalidSnapshot("Snapshot rewrites identity, metadata or transition history")
    except (ValueError, IndexError) as exc:
        raise InvalidSnapshot("Snapshot is not a legal successor") from exc
