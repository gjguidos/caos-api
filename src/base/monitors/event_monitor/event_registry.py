class EventRegistry:
    """Registry for dynamically managing event handlers."""

    _handlers = {}

    @classmethod
    def register_handler(cls, event_type: str, handler):
        cls._handlers[event_type] = handler

    @classmethod
    def get_registered_handlers(cls):
        return cls._handlers
