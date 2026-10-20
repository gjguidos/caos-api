import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("event configuration")


class EventConfig:
    def __init__(self, settings):
        """
        Initialize the EventConfig with a settings object.

        Args:
            settings: The settings object containing configuration values.
        """
        self.settings = settings
        self.handlers: dict[str, Callable[[dict[str, Any]], None] | None] = {}
        self.default_threshold = 10
        self.default_window_minutes = 5

    def set_handler(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """
        Set the handler for a specific event type.

        Args:
            event_type (str): The type of the event (e.g., "error", "rate_limit", "security").
            handler (Callable): The handler function for the event.
        """
        if not event_type:
            raise ValueError("Event type cannot be empty.")
        if not callable(handler):
            raise ValueError(f"Handler for event type '{event_type}' must be callable.")

        self.handlers[event_type] = handler
        logger.info(f"Handler for '{event_type}' event registered successfully.")

    def get_configuration(self) -> dict[str, Any]:
        """
        Get the event configuration, including handlers and thresholds.

        Returns:
            Dict[str, Any]: The event configuration.
        """
        # Ensure all handlers are set
        for event_type, handler in self.handlers.items():
            if handler is None:
                raise ValueError(f"Handler for '{event_type}' event is not set.")

        # Build the configuration dynamically
        configuration = {}
        for event_type, handler in self.handlers.items():
            configuration[event_type] = {
                "queue_name": self._get_queue_name(event_type),
                "threshold": self._get_threshold(event_type),
                "window_minutes": self._get_window_minutes(event_type),
                "handler": handler,
            }
        return configuration

    def _get_queue_name(self, event_type: str) -> str:
        """
        Get the queue name for a specific event type.

        Args:
            event_type (str): The type of the event.

        Returns:
            str: The queue name.
        """
        # Default to a generic monitoring queue if no specific queue is defined
        return getattr(self.settings, f"{event_type}_queue_name", "")

    def _get_threshold(self, event_type: str) -> int:
        """
        Get the threshold for a specific event type.

        Args:
            event_type (str): The type of the event.

        Returns:
            int: The threshold value.
        """
        return getattr(self.settings, f"{event_type}_threshold", self.default_threshold)

    def _get_window_minutes(self, event_type: str) -> int:
        """
        Get the window minutes for a specific event type.

        Args:
            event_type (str): The type of the event.

        Returns:
            int: The window minutes value.
        """
        return getattr(self.settings, f"{event_type}_window_minutes", self.default_window_minutes)
