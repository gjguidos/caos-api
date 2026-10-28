"""Pure transitions: describe state changes, never execute or authorize work."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from src.base.operations.v2.models import (
    AttemptState,
    OperationAttempt,
    OperationRecord,
    OperationState,
    Timestamp,
)

OPERATION_TRANSITIONS: Mapping[OperationState, frozenset[OperationState]] = MappingProxyType(
    {
        OperationState.PENDING: frozenset({OperationState.RUNNING, OperationState.FAILED, OperationState.CANCELLED}),
        OperationState.RUNNING: frozenset(
            {
                OperationState.SUCCEEDED,
                OperationState.FAILED,
                OperationState.RETRY_PENDING,
                OperationState.CANCEL_REQUESTED,
            }
        ),
        OperationState.RETRY_PENDING: frozenset({OperationState.RUNNING, OperationState.CANCELLED}),
        OperationState.CANCEL_REQUESTED: frozenset(
            {
                OperationState.SUCCEEDED,
                OperationState.FAILED,
                OperationState.CANCELLED,
            }
        ),
        OperationState.SUCCEEDED: frozenset(),
        OperationState.FAILED: frozenset(),
        OperationState.CANCELLED: frozenset(),
    }
)
ATTEMPT_TRANSITIONS: Mapping[AttemptState, frozenset[AttemptState]] = MappingProxyType(
    {
        AttemptState.RUNNING: frozenset(
            {
                AttemptState.SUCCEEDED,
                AttemptState.FAILED,
                AttemptState.CANCELLED,
            }
        ),
        AttemptState.SUCCEEDED: frozenset(),
        AttemptState.FAILED: frozenset(),
        AttemptState.CANCELLED: frozenset(),
    }
)
_TIME = TypeAdapter(Timestamp)


class InvalidTransition(ValueError):
    """A requested change contradicts the proposed lifecycle contract."""


def require_operation_transition(source: OperationState, target: OperationState) -> None:
    if not isinstance(source, OperationState) or not isinstance(target, OperationState):
        raise InvalidTransition("Operation transition guards require OperationState values")
    if target not in OPERATION_TRANSITIONS[source]:
        raise InvalidTransition(f"Illegal operation transition: {source} -> {target}")


def require_attempt_transition(source: AttemptState, target: AttemptState) -> None:
    if not isinstance(source, AttemptState) or not isinstance(target, AttemptState):
        raise InvalidTransition("Attempt transition guards require AttemptState values")
    if target not in ATTEMPT_TRANSITIONS[source]:
        raise InvalidTransition(f"Illegal attempt transition: {source} -> {target}")


def _evolve(operation: OperationRecord, *, at: datetime, **updates: Any) -> OperationRecord:
    at = _TIME.validate_python(at)
    if at < operation.updated_at:
        raise InvalidTransition("Transition time cannot move backwards")
    require_operation_transition(operation.state, updates["state"])
    # model_copy(update=...) bypasses Pydantic validation. Revalidate every new snapshot.
    return OperationRecord.model_validate(
        {
            **operation.model_dump(),
            **updates,
            "updated_at": at,
            "revision": operation.revision + 1,
        }
    )


def start_attempt(
    operation: OperationRecord, *, at: datetime, attempt_id: UUID | None = None
) -> OperationRecord:
    require_operation_transition(operation.state, OperationState.RUNNING)
    attempt = OperationAttempt(
        operation_id=operation.operation_id,
        attempt_id=uuid4() if attempt_id is None else attempt_id,
        attempt_number=len(operation.attempts) + 1,
        state=AttemptState.RUNNING,
        started_at=at,
    )
    return _evolve(
        operation, at=at, state=OperationState.RUNNING, attempts=(*operation.attempts, attempt)
    )


def finish_attempt(
    operation: OperationRecord,
    *,
    attempt_id: UUID,
    outcome: AttemptState,
    at: datetime,
    reason: str | None = None,
    retry: bool = False,
) -> OperationRecord:
    if operation.state not in {OperationState.RUNNING, OperationState.CANCEL_REQUESTED}:
        raise InvalidTransition("Only an active attempt can finish")
    if type(retry) is not bool:
        raise InvalidTransition("Retry intent must be an explicit boolean")
    if not isinstance(outcome, AttemptState) or not outcome.terminal:
        raise InvalidTransition("An attempt must finish with a terminal outcome")
    last = operation.attempts[-1]
    if last.attempt_id != attempt_id:
        raise InvalidTransition("Only the current attempt identity may report an outcome")
    require_attempt_transition(last.state, outcome)
    cancelling = operation.state == OperationState.CANCEL_REQUESTED
    if retry and (outcome == AttemptState.SUCCEEDED or cancelling):
        raise InvalidTransition("Success or operation cancellation intent cannot schedule a retry")

    finished = OperationAttempt.model_validate(
        {
            **last.model_dump(),
            "state": outcome,
            "finished_at": at,
            "reason": reason,
        }
    )
    if outcome == AttemptState.SUCCEEDED:
        target = OperationState.SUCCEEDED
    elif cancelling and outcome == AttemptState.CANCELLED:
        target = OperationState.CANCELLED
    elif retry:
        target = OperationState.RETRY_PENDING
    else:
        target = OperationState.FAILED
    return _evolve(
        operation,
        at=at,
        state=target,
        attempts=(*operation.attempts[:-1], finished),
        finished_at=at if target.terminal else None,
        failure_reason=reason if target == OperationState.FAILED else None,
    )


def fail_before_start(
    operation: OperationRecord, *, at: datetime, reason: str
) -> OperationRecord:
    """Fail an admitted operation before any execution attempt starts.

    For malformed or unauthorized requests, reject before creating an operation.
    This represents failure after an operation identity has been allocated,
    e.g. a missing workflow definition or unavailable preparation resource.
    """
    if operation.state != OperationState.PENDING or operation.attempts:
        raise InvalidTransition("Only a never-started pending operation can fail before start")
    return _evolve(
        operation,
        at=at,
        state=OperationState.FAILED,
        finished_at=at,
        failure_reason=reason,
    )


def ensure_cancellation_requested(
    operation: OperationRecord, *, at: datetime, reason: str
) -> OperationRecord:
    """Idempotent cancellation command adaptation for an identical repeated reason.

    A matching duplicate does not create a transition or increase revision.
    A conflicting reason is rejected. Real cross-worker deduplication and
    atomic persistence are future service/store responsibilities.
    """
    if operation.cancellation_requested_at is not None:
        if operation.cancellation_reason != reason:
            raise InvalidTransition("Cancellation was previously requested with another reason")
        if operation.state in {
            OperationState.CANCEL_REQUESTED,
            OperationState.CANCELLED,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
        }:
            return operation
    return request_cancellation(operation, at=at, reason=reason)


def request_cancellation(
    operation: OperationRecord, *, at: datetime, reason: str
) -> OperationRecord:
    """Record intent, not asyncio cancellation or proof of external quiescence.

    An idle operation has no active attempt and can terminate immediately.
    Active work remains CANCEL_REQUESTED until a confirmed outcome is recorded.
    """
    if operation.state not in {
        OperationState.PENDING,
        OperationState.RUNNING,
        OperationState.RETRY_PENDING,
    }:
        raise InvalidTransition(
            "Cancellation can be requested only once on a nonterminal operation"
        )
    target = (
        OperationState.CANCEL_REQUESTED
        if operation.state == OperationState.RUNNING
        else OperationState.CANCELLED
    )
    return _evolve(
        operation,
        at=at,
        state=target,
        cancellation_requested_at=at,
        cancellation_reason=reason,
        finished_at=at if target.terminal else None,
    )
