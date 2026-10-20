# src/base/events/models.py

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    event_id: str = Field(
        default_factory=lambda: f"evt_{uuid4().hex}"
    )
    event_type: str
    source: str
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    user_id: str | None = None
    tenant_id: str | None = None
    correlation_id: str | None = None

    data: dict[str, Any] = Field(default_factory=dict)