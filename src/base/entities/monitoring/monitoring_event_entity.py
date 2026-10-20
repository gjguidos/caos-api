import datetime
import uuid
from typing import Any


class MonitoringEvent:
    """Entity representing a monitoring event."""

    def __init__(self, event_type: str, details: dict[str, Any]):
        self.event_id = str(uuid.uuid4())  # Unique identifier for the event
        self.timestamp = datetime.datetime.now(timezone.utc).isoformat()  # Timestamp of the event
        self.event_type = event_type  # Type of the event (e.g., "error", "security")
        self.details = details  # Additional details about the event

    def to_dict(self) -> dict[str, Any]:
        """Convert the event to a dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "details": self.details,
        }
