from src.base.monitors.event_monitor.event_registry import EventRegistry
from src.base.monitors.process_error_event import process_error_event
from src.base.monitors.process_security_event import process_security_event


def register_event_handlers():
    """
    Register all event handlers with the EventRegistry.
    """
    EventRegistry.register_handler("application_error", process_error_event)
    EventRegistry.register_handler("unauthorized_access", process_security_event)
    EventRegistry.register_handler("rate_limit_violation", process_security_event)
