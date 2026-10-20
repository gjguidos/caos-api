from dependency_injector import containers, providers

from src.base.security.rate_limiter import create_rate_limiter


class RateLimiterContainer(containers.DeclarativeContainer):
    """
    Container for rate limiter-related dependencies.
    """

    # Rate limiter factory - allows creating rate limiters with different configurations
    rate_limiter_factory = providers.Factory(
        create_rate_limiter,
    )

    # Common rate limiters with standard configurations
    # These can be used directly or as a base for domain-specific limiters
    standard_api_rate_limiter = providers.Singleton(
        rate_limiter_factory,
        times=100,
        seconds=60,  # Default: 100 requests per minute
    )

    standard_write_rate_limiter = providers.Singleton(
        rate_limiter_factory,
        times=5,  # Limit: 5 writes per minute (to match test expectations)
        seconds=60,
    )

    standard_read_rate_limiter = providers.Singleton(
        rate_limiter_factory,
        times=100,
        seconds=60,  # Default: 100 reads per minute
    )
