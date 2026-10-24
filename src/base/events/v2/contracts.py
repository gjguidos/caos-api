from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Transport-safe event; signature lives in AMQP headers."""
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str
    schema_version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: str
    tenant_id: str
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPermissionError(PermissionError):
    pass


class EventContractError(ValueError):
    pass
