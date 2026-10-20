import logging

from src.base.config.config import settings

logger = logging.getLogger("Event handlers")


def handle_error_event(event_data: dict):
    if event_data.get("type") == "error":
        logger.error(f"Handling error event: {event_data}")
    else:
        logger.info(f"Ignoring non-error event: {event_data}")


def handle_rate_limit_event(event_data: dict):
    if event_data.get("type") == "rate_limit":
        logger.info(f"Handling rate limit event: {event_data}")
    else:
        logger.info(f"Ignoring non-rate-limit event: {event_data}")


def handle_security_event(event_data: dict):
    if event_data.get("type") == "security":
        logger.warning(f"Handling security event: {event_data}")
    else:
        logger.info(f"Ignoring non-security event: {event_data}")


def register_event_handlers():
    """
    Register event handlers for error, rate limit, and security events.
    """
    # Dynamically register handlers
    settings.event_config.set_handler("error", handle_error_event)
    settings.event_config.set_handler("rate_limit", handle_rate_limit_event)
    settings.event_config.set_handler("security", handle_security_event)
    logger.info("Event handlers registered successfully.")
