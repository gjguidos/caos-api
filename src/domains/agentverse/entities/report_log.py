"""
Operation Log Entities

OperationLog tracks CRUD operations - who did what, with the full payload.

Agent turn reports used to live here as MessageReport. They are now run
reports: see entities/run_report.py.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OperationType(str, Enum):
    """Types of operations tracked"""

    AGENT_CREATE = "agent_create"
    AGENT_UPDATE = "agent_update"
    AGENT_DELETE = "agent_delete"
    AGENT_READ = "agent_read"


class OperationLog(BaseModel):
    """
    Log entry for tracking CRUD operations.

    Tracks who performed what operation with the full payload.
    Used for auditing and tracking system changes.
    """

    id: str | None = Field(None, alias="_id")
    user_object_id: str | None = None  # ID of the user who performed the operation
    operation_type: OperationType
    payload: dict[str, Any] = Field(default_factory=dict)  # Full operation data
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    modified: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Config:
        populate_by_name = True
        use_enum_values = True


class OperationLogCreate(BaseModel):
    """Request model for creating an operation log"""

    user_object_id: str | None = None
    operation_type: OperationType
    payload: dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True
