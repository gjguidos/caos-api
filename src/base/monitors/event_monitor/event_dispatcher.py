"""
Event dispatcher for sending events to the EventMonitor.

This module provides a centralized way to dispatch events to the EventMonitor,
allowing loggers to focus solely on logging.
"""

import logging
from typing import Any

from src.base.monitors.event_monitor.event_monitor import EventMonitor

logger = logging.getLogger("event_dispatcher")


class EventDispatcher:
    """
    Centralized dispatcher for sending events to the EventMonitor.
    """

    def __init__(self, event_monitor: EventMonitor):
        self.event_monitor = event_monitor

    async def dispatch_event(self, event: dict[str, Any]) -> None:
        """
        Dispatch an event to the EventMonitor.

        Args:
            event: The event to dispatch.
        """
        try:
            logger.info(f"Dispatching event: {event.get('event_type', 'unknown')}")
            await self.event_monitor.process_event(event)
        except Exception as e:
            logger.error(f"Failed to dispatch event: {str(e)}")
