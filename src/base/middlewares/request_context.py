import logging
from contextvars import ContextVar

# Context variable to store request-scoped properties for logging.
# To add a new property, call set_request_context("key", value) from any middleware.
request_context: ContextVar[dict[str, str] | None] = ContextVar("request_context", default=None)


def set_request_context(key: str, value: str) -> None:
    """Set a key in the request context. Creates a new dict if needed."""
    ctx = request_context.get(None)
    if ctx is None:
        ctx = {}
        request_context.set(ctx)
    ctx[key] = value


def get_request_context(key: str, default: str = "") -> str:
    """Get a value from the request context."""
    ctx = request_context.get(None)
    if ctx is None:
        return default
    return ctx.get(key, default)


def reset_request_context() -> None:
    """Reset the request context. Call at the start of each request."""
    request_context.set(None)


class RequestContextFilter(logging.Filter):
    """Logging filter that adds all request context properties to log records."""

    def filter(self, record):
        ctx = request_context.get(None)
        if ctx:
            for key, value in ctx.items():
                setattr(record, key, value)
        return True
