"""
Green Claim Audit Entities

Tracks all green claim analysis requests for auditing and compliance purposes.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# Analysis type constants
ANALYSIS_TYPE_TEXT = "text"
ANALYSIS_TYPE_CHUNKED_TEXT = "chunked_text"
ANALYSIS_TYPE_FILE = "file"

AnalysisType = Literal["text", "chunked_text", "file"]


class GreenClaimAudit(BaseModel):
    """
    Audit log entry for green claim analysis requests.

    Tracks who performed what analysis, with configuration and results summary.
    Used for auditing, compliance tracking, and usage analytics.
    """

    id: str | None = Field(None, alias="_id")
    user_object_id: str | None = Field(
        None, description="ID of the user who requested the analysis"
    )
    analysis_type: AnalysisType = Field(
        ..., description="Type of analysis performed (text, chunked_text, file)"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the analysis was performed",
    )
    duration_ms: float = Field(..., description="Duration of the analysis in milliseconds", ge=0)
    input_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata about the input (text_length, file_hash, etc.)",
    )
    analysis_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuration used for analysis (model, temperature, etc.)",
    )
    output_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of analysis results (counts, violations, etc.)",
    )
    full_response: dict[str, Any] | None = Field(
        None, description="Complete analysis response (optional, can be large)"
    )
    error: str | None = Field(None, description="Error details if analysis failed")
    success: bool = Field(default=True, description="Whether the analysis completed successfully")
    created: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record creation timestamp",
    )
    modified: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record modification timestamp",
    )

    class Config:
        populate_by_name = True


class GreenClaimAuditCreate(BaseModel):
    """Request model for creating a green claim audit log"""

    user_object_id: str | None = None
    analysis_type: AnalysisType
    duration_ms: float = Field(..., ge=0)
    input_metadata: dict[str, Any] = Field(default_factory=dict)
    analysis_config: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    full_response: dict[str, Any] | None = None
    error: str | None = None
    success: bool = True


class GreenClaimAuditFilter(BaseModel):
    """Filter model for querying green claim audit logs"""

    user_object_id: str | None = None
    analysis_type: AnalysisType | None = None
    success: bool | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
