"""
Base rate limiter dependencies that can be used by any domain.

This module provides dependencies for commonly used rate limiters
to avoid reimplementing them in each domain.
"""

from src.base.security.rate_limiter import (
    default_rate_limiter,
    strict_rate_limiter,
    very_strict_rate_limiter,
)


def get_standard_api_rate_limiter():
    """
    Dependency that provides a standard API rate limiter.
    Limited to 100 requests per minute per client.

    Returns:
        A configured rate limiter
    """
    return default_rate_limiter  # 100 requests per minute


def get_standard_write_rate_limiter():
    """
    Dependency that provides a standard write operation rate limiter.
    Limited to 5 write operations per minute per client.

    Returns:
        A configured rate limiter
    """
    return very_strict_rate_limiter  # 5 requests per minute


def get_standard_read_rate_limiter():
    """
    Dependency that provides a standard read operation rate limiter.
    Limited to 100 read operations per minute per client.

    Returns:
        A configured rate limiter
    """
    return default_rate_limiter  # 100 requests per minute


def get_moderate_rate_limiter():
    """
    Dependency that provides a moderate rate limiter.
    Limited to 20 operations per minute per client.

    Returns:
        A configured rate limiter
    """
    return strict_rate_limiter  # 20 requests per minute


# If custom rate limiters are needed, they can be created directly with
# create_rate_limiter
