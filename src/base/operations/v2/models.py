"""Proposed OPS2-01 snapshots; no persistence, execution or authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    return value


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


NonBlankText = Annotated[StrictStr, AfterValidator(_nonblank)]
Timestamp = Annotated[AwareDatetime, AfterValidator(_utc)]


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_PENDING = "retry_pending"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class AttemptState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self != self.RUNNING


class OperationAttempt(BaseModel):
    """One execution, with an identity distinct from its operation and siblings."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    operation_id: UUID
    attempt_id: UUID
    attempt_number: Annotated[int, Field(strict=True, ge=1)]
    state: AttemptState
    started_at: Timestamp
    finished_at: Timestamp | None = None
    reason: NonBlankText | None = None

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.attempt_id == self.operation_id:
            raise ValueError("Attempt identity must differ from operation identity")
        if self.state.terminal != (self.finished_at is not None):
            raise ValueError("Only terminal attempts have a finished_at timestamp")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("Attempt cannot finish before it starts")
        needs_reason = self.state in {AttemptState.FAILED, AttemptState.CANCELLED}
        if needs_reason != (self.reason is not None):
            raise ValueError("Failed/cancelled attempts require a reason; other states forbid it")
        return self


class OperationRecord(BaseModel):
    """Immutable operation lifetime and ordered attempt history, ready to serialize.

    IDs/times/revision are required on restoration: parsing a damaged snapshot
    must not silently generate a new identity or clock value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    operation_id: UUID
    kind: NonBlankText
    producer: NonBlankText
    state: OperationState
    revision: Annotated[int, Field(strict=True, ge=0)]
    created_at: Timestamp
    updated_at: Timestamp
    attempts: tuple[OperationAttempt, ...] = ()
    correlation_id: NonBlankText | None = None
    causation_id: NonBlankText | None = None
    finished_at: Timestamp | None = None
    cancellation_requested_at: Timestamp | None = None
    cancellation_reason: NonBlankText | None = None
    failure_reason: NonBlankText | None = None

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        producer: str,
        at: datetime,
        operation_id: UUID | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Self:
        return cls(
            operation_id=uuid4() if operation_id is None else operation_id,
            kind=kind,
            producer=producer,
            state=OperationState.PENDING,
            revision=0,
            created_at=at,
            updated_at=at,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    @model_validator(mode="after")
    def consistent_lifetime(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Operation time cannot move before creation")
        if self.state.terminal != (self.finished_at is not None):
            raise ValueError("Only terminal operations have a finished_at timestamp")
        if self.finished_at is not None and self.finished_at != self.updated_at:
            raise ValueError("Terminal operation timestamps must agree")
        if (self.cancellation_requested_at is None) != (self.cancellation_reason is None):
            raise ValueError("Cancellation intent requires both a timestamp and reason")
        if (
            self.cancellation_requested_at is not None
            and not self.created_at <= self.cancellation_requested_at <= self.updated_at
        ):
            raise ValueError("Cancellation intent must be within the operation lifetime")
        if (self.state == OperationState.FAILED) != (self.failure_reason is not None):
            raise ValueError("Only failed operations require a failure reason")

        ids: set[UUID] = set()
        previous_finish = self.created_at
        for number, attempt in enumerate(self.attempts, start=1):
            if attempt.operation_id != self.operation_id or attempt.attempt_number != number:
                raise ValueError("Attempts must belong to this operation and be numbered from one")
            if attempt.attempt_id in ids:
                raise ValueError("Attempt identities cannot be reused")
            ids.add(attempt.attempt_id)
            if attempt.started_at < previous_finish:
                raise ValueError("Attempts cannot overlap or precede operation creation")
            if (attempt.finished_at or attempt.started_at) > self.updated_at:
                raise ValueError("Attempt timestamps cannot exceed operation updated_at")
            if number < len(self.attempts):
                if attempt.state not in {AttemptState.FAILED, AttemptState.CANCELLED}:
                    raise ValueError("Only unsuccessful terminal attempts can precede a retry")
                previous_finish = attempt.finished_at or attempt.started_at

        last = self.attempts[-1] if self.attempts else None
        if self.state == OperationState.PENDING:
            if self.attempts or self.revision != 0 or self.updated_at != self.created_at:
                raise ValueError("Pending is the initial state, before any attempt or transition")
        elif self.revision == 0:
            raise ValueError("A transitioned operation requires a positive revision")

        expected = {
            OperationState.RUNNING: {AttemptState.RUNNING},
            OperationState.CANCEL_REQUESTED: {AttemptState.RUNNING},
            OperationState.RETRY_PENDING: {AttemptState.FAILED, AttemptState.CANCELLED},
            OperationState.SUCCEEDED: {AttemptState.SUCCEEDED},
            OperationState.FAILED: {AttemptState.FAILED, AttemptState.CANCELLED},
        }
        if self.state in expected and not (
            # An operation can fail during preparation before any attempt starts.
            self.state == OperationState.FAILED and last is None
        ) and (last is None or last.state not in expected[self.state]):
            raise ValueError("Operation state does not agree with its latest attempt")
        if (
            self.state in {OperationState.SUCCEEDED, OperationState.FAILED}
            and last is not None
            and self.finished_at != last.finished_at
        ):
            raise ValueError("Operation completion must match its final attempt outcome time")
        if self.state == OperationState.CANCELLED and last is not None and not last.state.terminal:
            raise ValueError("Cancellation cannot be confirmed while an attempt is active")
        if (
            self.state == OperationState.CANCELLED
            and last is not None
            and last.state == AttemptState.SUCCEEDED
        ):
            raise ValueError("A successful attempt cannot be recorded as operation cancellation")

        cancellation_at = self.cancellation_requested_at
        requested = cancellation_at is not None
        minimum_revision = 2 * len(self.attempts) + int(requested)
        if last is not None and last.state == AttemptState.RUNNING:
            minimum_revision -= 1
        if self.revision < minimum_revision:
            raise ValueError("Revision undercounts the transitions implied by attempt history")
        if self.state in {OperationState.CANCEL_REQUESTED, OperationState.CANCELLED}:
            if not requested:
                raise ValueError(
                    "Cancellation states require recorded operation cancellation intent"
                )
        elif (
            self.state
            in {OperationState.PENDING, OperationState.RUNNING, OperationState.RETRY_PENDING}
            and requested
        ):
            raise ValueError("Cancellation intent cannot be discarded to continue or retry")
        if cancellation_at is not None and last is not None:
            if cancellation_at < last.started_at:
                raise ValueError(
                    "An attempt cannot start after operation cancellation was requested"
                )
            if (
                self.state == OperationState.CANCELLED
                and last.state == AttemptState.FAILED
                and last.finished_at is not None
                and cancellation_at < last.finished_at
            ):
                raise ValueError(
                    "A failed outcome after cancellation intent is failure, not cancellation"
                )
        return self
