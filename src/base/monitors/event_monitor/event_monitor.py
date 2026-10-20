import json
import logging
from typing import Any

from src.base.monitors.event_monitor.file_observer import LogFileObserver

logger = logging.getLogger("event_monitor")


class EventMonitor:
    """
    Monitors log files for events and dispatches them to the appropriate processors.
    """

    def __init__(self, log_paths: dict[str, str], dispatch_map: dict[str, Any]):
        """
        Initialize the EventMonitor.

        Args:
            log_paths: A dictionary mapping log types to their file paths.
            dispatch_map: A dictionary mapping event types to their processors.
        """
        self.log_paths = log_paths
        self.dispatch_map = dispatch_map
        self.observers = []

    def start_observers(self):
        """
        Start observing all log files.
        """
        for log_type, log_path in self.log_paths.items():
            observer = LogFileObserver(
                log_file_path=log_path,
                process_event=self.process_log_line,
            )
            observer.start()
            self.observers.append(observer)

    def stop_observers(self):
        """
        Stop observing all log files.
        """
        for observer in self.observers:
            observer.stop()

    def process_log_line(self, log_line: str):
        """
        Process a single log line.

        Args:
            log_line: The log line to process.
        """
        try:
            event = json.loads(log_line)
            event_type = event.get("event_type", "unknown")
            processor = self.dispatch_map.get(event_type)
            if processor:
                logger.info(f"Processing event: {event_type}")
                processor(event)
            else:
                logger.warning(f"No processor found for event type: {event_type}")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse log line: {log_line}")
