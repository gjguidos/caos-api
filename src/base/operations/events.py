from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .models import (
    OperationProgress,
    OperationStatus,
)


class OperationEventType(StrEnum):
    STARTED = "operation.started"
    PROGRESS = "operation.progress"
    COMPLETED = "operation.completed"
    FAILED = "operation.failed"
    CANCELLED = "operation.cancelled"


class OperationEvent(BaseModel):
    type: OperationEventType
    run_id: str
    kind: str
    producer: str
    status: OperationStatus

    progress_revision: int = 0

    message: str | None = None

    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None

    progress: OperationProgress | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )