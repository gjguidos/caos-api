import time
from typing import Any

from src.base.config.event_config import EventConfig


class EventFilteringService:
    """Service for filtering events based on thresholds, cooldowns, and observable log types."""

    def __init__(self, event_config: EventConfig):
        self.last_alert_time = {}  # Tracks the last alert time for each event type
        self.event_counts = {}  # Tracks event counts for thresholds
        self.event_config = event_config

    def should_create_event(self, event_type: str, details: dict[str, Any]) -> bool:
        """
        Determine whether an event should be created based on observable log types.

        Args:
            event_type (str): The type of the event (e.g., "error", "security").
            details (dict): Additional details about the event.

        Returns:
            bool: True if the event should be created, False otherwise.
        """
        # Get the event configuration
        config = self.event_config.get_configuration()

        # Check if the event type is in observable log types
        if event_type not in config:
            return False

        # Check thresholds
        if not self._check_threshold(event_type, config):
            return False

        # Check cooldowns
        if not self._check_cooldown(event_type, config):
            return False

        return True

    def _check_threshold(self, event_type: str, config: dict[str, Any]) -> bool:
        """
        Check if the event type exceeds the threshold.

        Args:
            event_type (str): The type of the event.
            config (dict): The event configuration.

        Returns:
            bool: True if the event is below the threshold, False otherwise.
        """
        threshold = config[event_type]["threshold"]
        self.event_counts[event_type] = self.event_counts.get(event_type, 0) + 1
        return self.event_counts[event_type] <= threshold

    def _check_cooldown(self, event_type: str, config: dict[str, Any]) -> bool:
        """
        Check if the event type is within the cooldown period.

        Args:
            event_type (str): The type of the event.
            config (dict): The event configuration.

        Returns:
            bool: True if the cooldown period has passed, False otherwise.
        """
        cooldown = config[event_type]["window_minutes"] * 60
        last_time = self.last_alert_time.get(event_type, 0)
        current_time = time.time()
        if current_time - last_time < cooldown:
            return False
        self.last_alert_time[event_type] = current_time
        return True
