from src.base.entities.monitoring.event_entities import Event


class EventFactory:
    @staticmethod
    def create_event(event_type: str, details: dict) -> Event:
        """
        Create an Event object based on the event type and details.

        Args:
            event_type (str): The type of the event (e.g., "error", "security").
            details (dict): Additional details about the event.

        Returns:
            Event: The created Event object.
        """
        return Event(event_type=event_type, details=details)
