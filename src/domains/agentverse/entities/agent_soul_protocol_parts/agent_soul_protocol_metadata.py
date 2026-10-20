from datetime import datetime

from pydantic import BaseModel, Field


class AgentSoulProfileMetadata(BaseModel):
    prototype_version: str | None = "1.0.0"
    unlock_phrase: str | None = None  # For "ritual" mode: symbolic or verbal key
    availability_schedule: dict[str, str] | None = (
        None  # {"from": "09:00", "to": "17:00"} or {"weekday": "Sunday"}
    )
    activation_conditions: dict[str, str] | None = (
        None  # e.g., {"location": "lab", "role": "developer"}
    )
    security_protocol_log: list[str] | None = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
