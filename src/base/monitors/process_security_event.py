import logging
from typing import Any

logger = logging.getLogger(__name__)


async def process_security_event(event: dict[str, Any]) -> None:
    """Process a security event and update counters."""
    event_type = event.get("event_type", "unknown")
    client_ip = event.get("client_ip", "unknown")
    endpoint = event.get("path", "unknown")

    if event_type == "unauthorized_access":
        logger.info(f"Unauthorized access detected from {client_ip}")
    elif event_type == "rate_limit_violation":
        logger.info(f"Rate limit violation detected on {endpoint}")
