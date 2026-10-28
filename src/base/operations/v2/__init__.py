"""OPS2-01 proposed contracts only; V1 remains unchanged and independently wired."""

from src.base.operations.v2.models import (
    AttemptState,
    OperationAttempt,
    OperationRecord,
    OperationState,
)
from src.base.operations.v2.transitions import (
    ATTEMPT_TRANSITIONS,
    OPERATION_TRANSITIONS,
    InvalidTransition,
    ensure_cancellation_requested,
    fail_before_start,
    finish_attempt,
    request_cancellation,
    require_attempt_transition,
    require_operation_transition,
    start_attempt,
)

__all__ = [
    "ATTEMPT_TRANSITIONS",
    "OPERATION_TRANSITIONS",
    "AttemptState",
    "InvalidTransition",
    "OperationAttempt",
    "OperationRecord",
    "OperationState",
    "ensure_cancellation_requested",
    "fail_before_start",
    "finish_attempt",
    "request_cancellation",
    "require_attempt_transition",
    "require_operation_transition",
    "start_attempt",
]