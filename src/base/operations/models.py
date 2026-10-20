from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class OperationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationProgress(BaseModel):
    phase: str | None = None
    current: int | None = None
    total: int | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationRecord(BaseModel):
    run_id: str
    kind: str
    producer: str

    status: OperationStatus = OperationStatus.PENDING

    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None

    progress: OperationProgress = Field(
        default_factory=OperationProgress
    )

    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)
    progress_revision: int = 0
    last_activity_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )