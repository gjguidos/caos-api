import logging
import os
from collections.abc import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("file_observer")


class LogFileHandler(FileSystemEventHandler):
    """
    Handler for monitoring log file changes.
    """

    def __init__(self, process_event: Callable[[str], None]):
        """
        Initialize the handler.

        Args:
            process_event: A callback function to process new log lines.
        """
        self.process_event = process_event

    def on_modified(self, event):
        """
        Called when a file is modified.
        """
        if event.is_directory:
            return
        logger.info(f"File modified: {event.src_path}")
        try:
            with open(event.src_path) as file:
                lines = file.readlines()
                if lines:
                    self.process_event(lines[-1].strip())  # Process the last line
        except Exception as e:
            logger.error(f"Failed to read file: {str(e)}")


class LogFileObserver:
    """
    Observer for monitoring log files.
    """

    def __init__(self, log_file_path: str, process_event: Callable[[str], None]):
        """
        Initialize the observer.

        Args:
            log_file_path: Path to the log file to monitor.
            process_event: A callback function to process new log lines.
        """
        self.log_file_path = log_file_path
        self.process_event = process_event
        self.observer = Observer()

    def start(self):
        """
        Start observing the log file.
        """
        if not os.path.exists(self.log_file_path):
            logger.warning(f"Log file does not exist: {self.log_file_path}. Skipping observer.")
            return
        event_handler = LogFileHandler(self.process_event)
        self.observer.schedule(event_handler, path=self.log_file_path, recursive=False)
        self.observer.start()
        logger.info(f"Started observing log file: {self.log_file_path}")

    def stop(self):
        """
        Stop observing the log file.
        """
        self.observer.stop()
        self.observer.join()
        logger.info(f"Stopped observing log file: {self.log_file_path}")
