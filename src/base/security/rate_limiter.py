"""
Rate limiter configuration for the API.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from fastapi_limiter.depends import RateLimiter

logger = logging.getLogger(__name__)

# Define standard rate limiters
default_rate_limiter = RateLimiter(times=100, seconds=60)  # 100 requests per minute
strict_rate_limiter = RateLimiter(times=20, seconds=60)  # 20 requests per minute
very_strict_rate_limiter = RateLimiter(times=5, seconds=60)  # 5 requests per minute


# Create a custom rate limiter with logging
class LoggingRateLimiter(RateLimiter):
    """
    Extended RateLimiter with logging capabilities
    """

    def __init__(
        self, times: int, seconds: int, identifier: Callable | None = None, endpoint: str = None
    ):
        """Initialize with rate limit parameters and optional endpoint name"""
        super().__init__(times=times, seconds=seconds, identifier=identifier)
        self.endpoint = endpoint

    async def __call__(self, request: Request, response: Response = None):
        """
        Apply rate limiting with additional logging

        Args:
            request: The FastAPI request object
            response: The FastAPI response object (optional)

        Returns:
            True if the rate limit is not exceeded
        """
        # Record the endpoint from the request if not provided during initialization
        if not self.endpoint:
            self.endpoint = request.url.path

        try:
            # Get the client identifier
            identifier_func = self.identifier or ip_identifier
            client_id = identifier_func(request)

            # We make the actual rate limit check
            return await super().__call__(request, response)

        except HTTPException as exc:
            # When rate limit is exceeded, log it
            if exc.status_code == 429:  # Too Many Requests
                if hasattr(self, "times") and hasattr(self, "seconds"):
                    # Use the specialized rate limit logger instead of the general logger
                    logger.warning(
                        f"Rate limit exceeded: {self.endpoint} | Client: {client_id} | Limit: {self.times}/{self.seconds}s"
                    )
            # Re-raise the exception
            raise


def create_rate_limiter(
    times: int, seconds: int, identifier: Callable | None = None, endpoint: str | None = None
) -> RateLimiter:
    """
    Create a custom rate limiter with specified parameters.

    Args:
        times: Number of requests allowed
        seconds: Time period in seconds
        identifier: Optional custom identifier function
        endpoint: Optional endpoint name for logging

    Returns:
        A configured RateLimiter dependency
    """
    return LoggingRateLimiter(
        times=times, seconds=seconds, identifier=identifier, endpoint=endpoint
    )


def ip_identifier(request: Request) -> str:
    """
    Get client IP address for rate limiting.
    Handles X-Forwarded-For header for proxied requests.

    Args:
        request: The FastAPI request object

    Returns:
        The client IP address
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Get the first IP in the chain (client IP)
        client_ip = forwarded.split(",")[0].strip()
        return f"ip:{client_ip}"
    return f"ip:{request.client.host}"


def api_key_identifier(request: Request) -> str:
    """
    Get API key for rate limiting.

    Args:
        request: The FastAPI request object

    Returns:
        The API key or a default value
    """
    api_key = request.headers.get("X-API-Key") or "anonymous"
    return f"api_key:{api_key}"


# Custom rate limiters with different identifiers
ip_rate_limiter = create_rate_limiter(100, 60, ip_identifier)
api_key_rate_limiter = create_rate_limiter(200, 60, api_key_identifier)


# Instead of trying to chain dependencies, we'll use a simpler approach
# by creating a class that combines multiple rate limiters
class CombinedRateLimiter:
    """
    A class that combines multiple rate limiters.
    """

    def __init__(self, limiters: list[RateLimiter]):
        """
        Initialize with a list of rate limiters.

        Args:
            limiters: List of RateLimiter instances to apply
        """
        self.limiters = limiters

    async def __call__(self, request: Request):
        """
        Apply all rate limiters in sequence.

        Args:
            request: The FastAPI request object

        Returns:
            True if all rate limiters pass
        """
        for limiter in self.limiters:
            await limiter(request)
        return True


# Create a combined rate limiter instance
combined_rate_limiter = CombinedRateLimiter([ip_rate_limiter, api_key_rate_limiter])


# Decorator for applying rate limiting to endpoint functions
def rate_limited(
    limiter: RateLimiter | None = None,
    times: int | None = None,
    seconds: int | None = None,
):
    """
    Decorator for applying rate limiting to endpoint functions.

    Args:
        limiter: Optional pre-configured RateLimiter to use
        times: Number of requests allowed (if creating a new limiter)
        seconds: Time period in seconds (if creating a new limiter)

    Returns:
        A decorator function

    Example:
        @app.get("/my-endpoint")
        @rate_limited(times=10, seconds=60)
        async def my_endpoint():
            return {"message": "Rate limited endpoint"}
    """

    def decorator(func: Callable) -> Callable:
        # Get the endpoint name from the function
        endpoint_name = f"{func.__module__}.{func.__name__}"

        if limiter is None and (times is None or seconds is None):
            # Use default rate limiter if no parameters provided
            selected_limiter = default_rate_limiter
        elif limiter is None:
            # Create a new rate limiter with the provided parameters and endpoint name
            selected_limiter = create_rate_limiter(times, seconds, endpoint=endpoint_name)
        else:
            # Use the provided limiter
            selected_limiter = limiter

        # Create a dependency that will be used by FastAPI
        dependency = Depends(selected_limiter)

        # Store the original signature
        original_signature = func.__annotations__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # The dependency will be injected by FastAPI before this function is called
            return await func(*args, **kwargs)

        # Add the dependency to the wrapper function's signature
        # This is the key part that makes FastAPI recognize the dependency
        if hasattr(wrapper, "__dependencies__"):
            wrapper.__dependencies__.append(dependency)
        else:
            wrapper.__dependencies__ = [dependency]

        # Preserve the original signature
        wrapper.__annotations__ = original_signature

        return wrapper

    return decorator
