import logging
from typing import Any

logger = logging.getLogger(__name__)


def process_error_event(event: dict[str, Any]) -> None:
    """Process an error event and update counters."""
    error_module = event.get("module", "unknown")
    error_message = event.get("message", "")

    # Log the error pattern detection
    logger.info(f"Error pattern detected in module {error_module}: {error_message[:100]}...")
