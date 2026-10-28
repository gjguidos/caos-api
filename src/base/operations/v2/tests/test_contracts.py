"""Deterministic contract tests; no runner, datastore, broker or authorization."""

from datetime import UTC, datetime, timedelta, timezone
from itertools import product
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.base.operations.v2 import (
    ATTEMPT_TRANSITIONS,
    OPERATION_TRANSITIONS,
    AttemptState,
    InvalidTransition,
    OperationAttempt,
    OperationRecord,
    OperationState,
    finish_attempt,
    request_cancellation,
    require_attempt_transition,
    require_operation_transition,
    start_attempt,
)

T0 = datetime(2026, 10, 28, tzinfo=UTC)
T1, T2, T3, T4 = (T0 + timedelta(seconds=i) for i in range(1, 5))
OP_ID, FIRST, SECOND, OTHER = (UUID(int=i) for i in range(1, 5))


def pending() -> OperationRecord:
    return OperationRecord.create(
        operation_id=OP_ID,
        kind="example",
        producer="test",
        at=T0,
        correlation_id="correlation",
        causation_id="parent",
    )


def running() -> OperationRecord:
    return start_attempt(pending(), at=T1, attempt_id=FIRST)


def retry_pending() -> OperationRecord:
    return finish_attempt(
        running(),
        attempt_id=FIRST,
        outcome=AttemptState.FAILED,
        reason="temporary failure",
        retry=True,
        at=T2,
    )


def cancelling() -> OperationRecord:
    return request_cancellation(running(), at=T2, reason="caller requested stop")


def terminal(state: OperationState) -> OperationRecord:
    if state == OperationState.CANCELLED:
        return finish_attempt(
            cancelling(),
            attempt_id=FIRST,
            outcome=AttemptState.CANCELLED,
            at=T3,
            reason="executor confirmed cessation",
        )
    return finish_attempt(
        running(),
        attempt_id=FIRST,
        outcome=AttemptState(state.value),
        at=T2,
        reason="failed" if state == OperationState.FAILED else None,
    )


def test_fresh_identity_generation_is_explicit_and_separate() -> None:
    first = OperationRecord.create(kind="example", producer="test", at=T0)
    second = OperationRecord.create(kind="example", producer="test", at=T0)
    active = start_attempt(first, at=T1)
    assert first.operation_id.version == second.operation_id.version == 4
    assert first.operation_id != second.operation_id
    assert active.operation_id == first.operation_id
    assert active.attempts[0].attempt_id.version == 4
    assert active.attempts[0].attempt_id != active.operation_id
    assert active.attempts[0].operation_id == first.operation_id
    assert active.attempts[0].attempt_number == 1
    assert first.state == OperationState.PENDING and first.attempts == ()


@pytest.mark.parametrize("state", list(OperationState))
def test_operation_terminal_classification(state: OperationState) -> None:
    assert state.terminal == (
        state in {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED}
    )


@pytest.mark.parametrize("state", list(AttemptState))
def test_attempt_terminal_classification(state: AttemptState) -> None:
    assert state.terminal == (state != AttemptState.RUNNING)


@pytest.mark.parametrize("source,target", list(product(OperationState, repeat=2)))
def test_every_operation_transition(source: OperationState, target: OperationState) -> None:
    edges = {
        (OperationState.PENDING, OperationState.RUNNING),
        (OperationState.PENDING, OperationState.CANCELLED),
        (OperationState.PENDING, OperationState.FAILED),
        (OperationState.RUNNING, OperationState.SUCCEEDED),
        (OperationState.RUNNING, OperationState.FAILED),
        (OperationState.RUNNING, OperationState.RETRY_PENDING),
        (OperationState.RUNNING, OperationState.CANCEL_REQUESTED),
        (OperationState.RETRY_PENDING, OperationState.RUNNING),
        (OperationState.RETRY_PENDING, OperationState.CANCELLED),
        (OperationState.CANCEL_REQUESTED, OperationState.SUCCEEDED),
        (OperationState.CANCEL_REQUESTED, OperationState.FAILED),
        (OperationState.CANCEL_REQUESTED, OperationState.CANCELLED),
    }
    if (source, target) in edges:
        require_operation_transition(source, target)
    else:
        with pytest.raises(InvalidTransition):
            require_operation_transition(source, target)


@pytest.mark.parametrize("source,target", list(product(AttemptState, repeat=2)))
def test_every_attempt_transition(source: AttemptState, target: AttemptState) -> None:
    if source == AttemptState.RUNNING and target != AttemptState.RUNNING:
        require_attempt_transition(source, target)
    else:
        with pytest.raises(InvalidTransition):
            require_attempt_transition(source, target)


def test_transition_tables_are_read_only() -> None:
    with pytest.raises(TypeError):
        OPERATION_TRANSITIONS[OperationState.FAILED] = frozenset()  # type: ignore[index]
    with pytest.raises(TypeError):
        ATTEMPT_TRANSITIONS[AttemptState.FAILED] = frozenset()  # type: ignore[index]


def test_transition_guards_reject_untyped_or_v1_states() -> None:
    from src.base.operations.models import OperationStatus as V1Status

    with pytest.raises(InvalidTransition):
        require_operation_transition(V1Status.PENDING, OperationState.RUNNING)  # type: ignore[arg-type]
    with pytest.raises(InvalidTransition):
        require_operation_transition(OperationState.PENDING, "running")  # type: ignore[arg-type]
    with pytest.raises(InvalidTransition):
        require_attempt_transition("unknown", AttemptState.FAILED)  # type: ignore[arg-type]


def test_success_retains_identity_metadata_and_clock_history() -> None:
    initial = pending()
    active = start_attempt(initial, at=T1, attempt_id=FIRST)
    complete = finish_attempt(active, attempt_id=FIRST, outcome=AttemptState.SUCCEEDED, at=T2)
    assert initial.revision == 0 and active.revision == 1 and complete.revision == 2
    assert complete.operation_id == OP_ID
    assert complete.created_at == T0 and complete.updated_at == complete.finished_at == T2
    assert complete.attempts[0].started_at == T1 and complete.attempts[0].finished_at == T2
    assert complete.correlation_id == "correlation" and complete.causation_id == "parent"
    assert active.attempts[0].state == AttemptState.RUNNING
    assert active.finished_at is None and initial.attempts == ()


def test_retry_uses_new_identity_and_contiguous_number_without_rewriting_history() -> None:
    waiting = retry_pending()
    resumed = start_attempt(waiting, attempt_id=SECOND, at=T3)
    complete = finish_attempt(resumed, attempt_id=SECOND, outcome=AttemptState.SUCCEEDED, at=T4)
    assert waiting.state == OperationState.RETRY_PENDING and waiting.finished_at is None
    assert waiting.failure_reason is None and waiting.attempts[0].reason == "temporary failure"
    assert [attempt.attempt_number for attempt in complete.attempts] == [1, 2]
    assert [attempt.attempt_id for attempt in complete.attempts] == [FIRST, SECOND]
    assert complete.operation_id == waiting.operation_id == OP_ID
    assert complete.attempts[0] == waiting.attempts[0]
    assert complete.revision == 4
    with pytest.raises(ValidationError, match="identities cannot be reused"):
        start_attempt(waiting, attempt_id=FIRST, at=T3)
    with pytest.raises(InvalidTransition, match="current attempt"):
        finish_attempt(resumed, attempt_id=FIRST, outcome=AttemptState.SUCCEEDED, at=T4)


def test_attempts_cannot_overlap_or_skip_a_failed_outcome() -> None:
    with pytest.raises(InvalidTransition):
        start_attempt(running(), at=T2, attempt_id=SECOND)
    with pytest.raises(InvalidTransition):
        finish_attempt(pending(), attempt_id=FIRST, outcome=AttemptState.FAILED, at=T1, retry=True)
    with pytest.raises(InvalidTransition):
        finish_attempt(running(), attempt_id=FIRST, outcome=AttemptState.RUNNING, at=T2)


def test_more_than_one_retry_keeps_contiguous_history_and_accepts_equal_clock_ticks() -> None:
    second = start_attempt(retry_pending(), attempt_id=SECOND, at=T3)
    waiting = finish_attempt(
        second,
        attempt_id=SECOND,
        outcome=AttemptState.FAILED,
        at=T4,
        reason="another temporary failure",
        retry=True,
    )
    third = start_attempt(waiting, attempt_id=OTHER, at=T4)
    assert [attempt.attempt_number for attempt in third.attempts] == [1, 2, 3]
    assert [attempt.attempt_id for attempt in third.attempts] == [FIRST, SECOND, OTHER]
    assert third.attempts[:-1] == waiting.attempts
    assert third.revision == 5


def test_retry_after_invocation_cancellation_allocates_a_new_attempt() -> None:
    waiting = finish_attempt(
        running(),
        attempt_id=FIRST,
        outcome=AttemptState.CANCELLED,
        at=T2,
        reason="invocation interrupted",
        retry=True,
    )
    resumed = start_attempt(waiting, attempt_id=SECOND, at=T3)
    assert resumed.state == OperationState.RUNNING
    assert resumed.attempts[0] == waiting.attempts[0]
    assert resumed.attempts[0].state == AttemptState.CANCELLED
    assert resumed.attempts[1].attempt_id == SECOND
    assert resumed.attempts[1].attempt_number == 2


@pytest.mark.parametrize("retry", [False, True])
def test_invocation_cancellation_is_not_operation_cancellation(retry: bool) -> None:
    result = finish_attempt(
        running(),
        attempt_id=FIRST,
        outcome=AttemptState.CANCELLED,
        at=T2,
        reason="upstream invocation cancelled",
        retry=retry,
    )
    assert result.state == (OperationState.RETRY_PENDING if retry else OperationState.FAILED)
    assert result.attempts[0].state == AttemptState.CANCELLED
    assert result.cancellation_requested_at is None and result.cancellation_reason is None


@pytest.mark.parametrize("idle", [pending(), retry_pending()])
def test_idle_operation_cancellation_has_no_active_work(idle: OperationRecord) -> None:
    result = request_cancellation(idle, at=T3, reason="no longer needed")
    assert result.state == OperationState.CANCELLED
    assert result.attempts == idle.attempts
    assert result.cancellation_requested_at == result.finished_at == T3
    assert result.cancellation_reason == "no longer needed"


def test_cancellation_request_is_not_confirmation_or_task_cancellation() -> None:
    requested = cancelling()
    assert requested.state == OperationState.CANCEL_REQUESTED
    assert requested.finished_at is None and requested.attempts[0].state == AttemptState.RUNNING
    assert requested.attempts[0].finished_at is None
    with pytest.raises(InvalidTransition):
        start_attempt(requested, at=T3)
    with pytest.raises(InvalidTransition):
        request_cancellation(requested, at=T3, reason="repeat")
    confirmed = finish_attempt(
        requested,
        attempt_id=FIRST,
        outcome=AttemptState.CANCELLED,
        at=T3,
        reason="executor confirmed cessation",
    )
    assert confirmed.state == OperationState.CANCELLED
    assert confirmed.cancellation_requested_at == T2 and confirmed.finished_at == T3
    assert requested.attempts[0].state == AttemptState.RUNNING


def test_cancellation_and_confirmation_can_share_a_clock_tick() -> None:
    complete = finish_attempt(
        cancelling(),
        attempt_id=FIRST,
        outcome=AttemptState.CANCELLED,
        at=T2,
        reason="executor confirmed cessation",
    )
    assert complete.cancellation_requested_at == complete.finished_at == T2
    assert complete.revision == 3


@pytest.mark.parametrize("outcome", [AttemptState.SUCCEEDED, AttemptState.FAILED])
def test_real_completion_can_win_cancellation_race_without_discarding_intent(
    outcome: AttemptState,
) -> None:
    result = finish_attempt(
        cancelling(),
        attempt_id=FIRST,
        outcome=outcome,
        at=T3,
        reason="actual failure" if outcome == AttemptState.FAILED else None,
    )
    assert result.state == OperationState(outcome.value)
    assert result.cancellation_requested_at == T2
    assert result.cancellation_reason == "caller requested stop"


def test_failed_outcome_during_cancellation_cannot_be_relabeled_cancelled() -> None:
    failed = finish_attempt(
        cancelling(), attempt_id=FIRST, outcome=AttemptState.FAILED, at=T3, reason="actual failure"
    )
    with pytest.raises(ValidationError, match="failure, not cancellation"):
        OperationRecord.model_validate(
            {
                **failed.model_dump(),
                "state": OperationState.CANCELLED,
                "failure_reason": None,
            }
        )


@pytest.mark.parametrize("outcome", list(AttemptState))
def test_retry_forbidden_after_operation_cancellation_intent(outcome: AttemptState) -> None:
    with pytest.raises(InvalidTransition):
        finish_attempt(cancelling(), attempt_id=FIRST, outcome=outcome, at=T3, retry=True)


def test_success_cannot_be_retried_and_retry_boolean_is_not_coerced() -> None:
    with pytest.raises(InvalidTransition):
        finish_attempt(
            running(), attempt_id=FIRST, outcome=AttemptState.SUCCEEDED, at=T2, retry=True
        )
    with pytest.raises(InvalidTransition, match="boolean"):
        finish_attempt(
            running(),
            attempt_id=FIRST,
            outcome=AttemptState.FAILED,
            at=T2,
            reason="failure",
            retry="yes",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "state", [OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED]
)
def test_terminal_snapshots_are_frozen_and_all_commands_reject_changes(
    state: OperationState,
) -> None:
    operation = terminal(state)
    original = operation.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        operation.state = OperationState.PENDING
    with pytest.raises(ValidationError, match="frozen"):
        operation.attempts[0].reason = "changed"
    with pytest.raises(InvalidTransition):
        start_attempt(operation, at=T4)
    with pytest.raises(InvalidTransition):
        request_cancellation(operation, at=T4, reason="late")
    with pytest.raises(InvalidTransition):
        finish_attempt(
            operation, attempt_id=FIRST, outcome=AttemptState.FAILED, at=T4, reason="late"
        )
    assert operation.model_dump_json() == original


@pytest.mark.parametrize(
    "source",
    [
        pending(),
        running(),
        retry_pending(),
        cancelling(),
        *(
            terminal(state)
            for state in (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED)
        ),
    ],
)
def test_json_round_trip_preserves_all_identities_and_outcomes(source: OperationRecord) -> None:
    restored = OperationRecord.model_validate_json(source.model_dump_json())
    assert restored == source
    assert restored.operation_id == OP_ID
    assert isinstance(restored.attempts, tuple)


@pytest.mark.parametrize("field", ["operation_id", "revision", "created_at", "updated_at"])
def test_restore_does_not_generate_missing_operation_identity_or_clock(field: str) -> None:
    data = pending().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        OperationRecord.model_validate(data)


@pytest.mark.parametrize("field", ["operation_id", "attempt_id", "attempt_number", "started_at"])
def test_restore_does_not_generate_missing_attempt_identity_or_clock(field: str) -> None:
    data = running().attempts[0].model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        OperationAttempt.model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"operation_id": "invalid"},
        {"kind": ""},
        {"producer": "   "},
        {"correlation_id": ""},
        {"causation_id": " "},
        {"state": "unknown"},
        {"revision": -1},
        {"revision": True},
        {"revision": "1"},
        {"created_at": T0.replace(tzinfo=None)},
        {"updated_at": T0 - timedelta(seconds=1)},
        {"finished_at": T1},
        {"cancellation_requested_at": T0},
        {"cancellation_reason": "stop"},
        {"failure_reason": "bad"},
        {"tenant_grants": {}},
    ],
)
def test_invalid_operation_construction(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OperationRecord.model_validate({**pending().model_dump(), **updates})


@pytest.mark.parametrize(
    "updates",
    [
        {"attempt_id": "invalid"},
        {"attempt_id": OP_ID},
        {"attempt_number": 0},
        {"attempt_number": -1},
        {"attempt_number": True},
        {"attempt_number": "1"},
        {"attempt_number": 1.5},
        {"state": "unknown"},
        {"started_at": T1.replace(tzinfo=None)},
        {"finished_at": T2},
        {"reason": "unexpected"},
        {"state": AttemptState.FAILED},
        {"state": AttemptState.CANCELLED, "finished_at": T2, "reason": " "},
        {"state": AttemptState.FAILED, "finished_at": T0, "reason": "failure"},
        {"permission": "execute"},
    ],
)
def test_invalid_attempt_construction(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OperationAttempt.model_validate({**running().attempts[0].model_dump(), **updates})


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "foreign",
        "gap",
        "active_previous",
        "successful_previous",
        "overlap",
        "future",
    ],
)
def test_inconsistent_attempt_history_is_rejected(change: str) -> None:
    data = start_attempt(retry_pending(), attempt_id=SECOND, at=T3).model_dump()
    first, second = data["attempts"]
    if change == "duplicate":
        second["attempt_id"] = first["attempt_id"]
    elif change == "foreign":
        first["operation_id"] = OTHER
    elif change == "gap":
        second["attempt_number"] = 3
    elif change == "active_previous":
        first.update(state=AttemptState.RUNNING, finished_at=None, reason=None)
    elif change == "successful_previous":
        first.update(state=AttemptState.SUCCEEDED, reason=None)
    elif change == "overlap":
        second["started_at"] = T1
    else:
        second["started_at"] = T4
    with pytest.raises(ValidationError):
        OperationRecord.model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"state": OperationState.PENDING},
        {"state": OperationState.SUCCEEDED},
        {"state": OperationState.RETRY_PENDING},
        {"state": OperationState.CANCELLED, "finished_at": T1},
        {"state": OperationState.CANCEL_REQUESTED},
        {"revision": 0},
        {
            "state": OperationState.RUNNING,
            "cancellation_requested_at": T1,
            "cancellation_reason": "stop",
        },
    ],
)
def test_operation_and_attempt_states_must_agree(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OperationRecord.model_validate({**running().model_dump(), **updates})


@pytest.mark.parametrize(
    "state", [OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED]
)
def test_terminal_operation_requires_matching_completion_timestamp(state: OperationState) -> None:
    data = terminal(state).model_dump()
    with pytest.raises(ValidationError):
        OperationRecord.model_validate({**data, "finished_at": None})
    with pytest.raises(ValidationError):
        OperationRecord.model_validate({**data, "updated_at": T4})


@pytest.mark.parametrize("state", [OperationState.SUCCEEDED, OperationState.FAILED])
def test_final_attempt_and_operation_completion_cannot_disagree(state: OperationState) -> None:
    data = terminal(state).model_dump()
    with pytest.raises(ValidationError, match="final attempt outcome time"):
        OperationRecord.model_validate({**data, "finished_at": T4, "updated_at": T4})
    with pytest.raises(ValidationError):
        OperationRecord.model_validate(
            {
                **data,
                "finished_at": T4,
                "updated_at": T4,
                "cancellation_requested_at": T3,
                "cancellation_reason": "too late",
            }
        )


def test_pending_clock_cannot_advance_without_a_transition() -> None:
    with pytest.raises(ValidationError, match="initial state"):
        OperationRecord.model_validate({**pending().model_dump(), "updated_at": T1})


@pytest.mark.parametrize(
    "operation",
    [
        retry_pending(),
        cancelling(),
        terminal(OperationState.SUCCEEDED),
        terminal(OperationState.FAILED),
        terminal(OperationState.CANCELLED),
        start_attempt(retry_pending(), attempt_id=SECOND, at=T3),
    ],
)
def test_revision_cannot_undercount_visible_history(operation: OperationRecord) -> None:
    with pytest.raises(ValidationError, match="undercounts"):
        OperationRecord.model_validate({**operation.model_dump(), "revision": 1})


def test_cancellation_and_failure_reasons_cannot_be_missing_or_blank() -> None:
    active = running()
    with pytest.raises(ValidationError):
        request_cancellation(active, at=T2, reason=" ")
    with pytest.raises(ValidationError):
        finish_attempt(active, attempt_id=FIRST, outcome=AttemptState.FAILED, at=T2)
    with pytest.raises(ValidationError):
        finish_attempt(cancelling(), attempt_id=FIRST, outcome=AttemptState.CANCELLED, at=T3)
    assert active.state == OperationState.RUNNING and active.revision == 1


def test_times_are_aware_normalized_and_cannot_move_backwards() -> None:
    offset = timezone(timedelta(hours=2))
    operation = OperationRecord.create(kind="example", producer="test", at=T0.astimezone(offset))
    assert operation.created_at == T0 and operation.created_at.tzinfo is UTC
    with pytest.raises(InvalidTransition, match="backwards"):
        start_attempt(operation, at=T0 - timedelta(seconds=1))
    with pytest.raises(InvalidTransition, match="backwards"):
        request_cancellation(running(), at=T0, reason="stop")
    with pytest.raises(ValidationError):
        request_cancellation(running(), at=T2.replace(tzinfo=None), reason="stop")
    with pytest.raises(ValueError):
        finish_attempt(cancelling(), attempt_id=FIRST, outcome=AttemptState.SUCCEEDED, at=T1)


def test_v1_models_remain_independent_and_mutable() -> None:
    from src.base.operations.models import OperationRecord as V1Record
    from src.base.operations.models import OperationStatus as V1Status

    legacy = V1Record(run_id="legacy-id", kind="example", producer="test")
    assert not issubclass(OperationRecord, V1Record)
    assert legacy.status == V1Status.PENDING
    legacy.status = V1Status.COMPLETED
    assert legacy.model_dump(mode="json")["status"] == "completed"
    assert pending().state == OperationState.PENDING


# OPS2-01 acceptance addendum: pre-start failure and idempotent cancellation.
def test_fail_before_start_preserves_identity_without_attempts():
    from datetime import UTC, datetime, timedelta

    from src.base.operations.v2.models import OperationRecord, OperationState
    from src.base.operations.v2.transitions import fail_before_start

    initial = OperationRecord.create(
        kind="test", producer="test", at=datetime(2026, 10, 28, tzinfo=UTC)
    )
    failed = fail_before_start(
        initial, at=initial.created_at + timedelta(seconds=1), reason="definition unavailable"
    )
    assert failed.operation_id == initial.operation_id
    assert failed.state is OperationState.FAILED
    assert failed.attempts == ()
    assert failed.revision == 1
    assert failed.failure_reason == "definition unavailable"
    assert failed.finished_at == failed.updated_at
    assert OperationRecord.model_validate_json(failed.model_dump_json()) == failed


def test_fail_before_start_requires_pending():
    from datetime import UTC, datetime

    import pytest

    from src.base.operations.v2.models import OperationRecord
    from src.base.operations.v2.transitions import (
        InvalidTransition,
        fail_before_start,
        start_attempt,
    )

    initial = OperationRecord.create(kind="test", producer="test", at=datetime(2026, 10, 28, tzinfo=UTC))
    active = start_attempt(initial, at=initial.created_at)
    with pytest.raises(InvalidTransition):
        fail_before_start(active, at=initial.created_at, reason="no")


def test_duplicate_cancellation_keeps_revision_and_timestamp():
    from datetime import UTC, datetime, timedelta

    import pytest

    from src.base.operations.v2.models import OperationRecord
    from src.base.operations.v2.transitions import (
        InvalidTransition,
        ensure_cancellation_requested,
        start_attempt,
    )

    initial = OperationRecord.create(kind="test", producer="test", at=datetime(2026, 10, 28, tzinfo=UTC))
    active = start_attempt(initial, at=initial.created_at)
    first = ensure_cancellation_requested(active, at=active.updated_at, reason="owner requested")
    duplicate = ensure_cancellation_requested(
        first, at=first.updated_at + timedelta(seconds=10), reason="owner requested"
    )
    assert duplicate is first
    assert duplicate.revision == first.revision
    assert duplicate.cancellation_requested_at == first.cancellation_requested_at
    with pytest.raises(InvalidTransition):
        ensure_cancellation_requested(duplicate, at=duplicate.updated_at, reason="other reason")


def test_duplicate_cancellation_after_immediate_cancel():
    from datetime import UTC, datetime

    from src.base.operations.v2.models import OperationRecord, OperationState
    from src.base.operations.v2.transitions import ensure_cancellation_requested

    initial = OperationRecord.create(kind="test", producer="test", at=datetime(2026, 10, 28, tzinfo=UTC))
    cancelled = ensure_cancellation_requested(initial, at=initial.created_at, reason="user")
    assert cancelled.state == OperationState.CANCELLED
    assert ensure_cancellation_requested(cancelled, at=cancelled.updated_at, reason="user") is cancelled
