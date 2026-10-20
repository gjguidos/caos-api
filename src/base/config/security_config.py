from pydantic import field_validator

from src.base.config.utils.utils import parse_comma_separated_list


class SecurityConfig:
    """
    Security and rate-limiting settings.
    """

    @field_validator("email_recipients", mode="before")
    @classmethod
    def parse_email_recipients(cls, v):
        return parse_comma_separated_list(v)

    # Rate limiting settings
    standard_rate_limit: int = 100
    standard_rate_limit_window: int = 60
    strict_rate_limit: int = 20
    strict_rate_limit_window: int = 60
    very_strict_rate_limit: int = 5
    very_strict_rate_limit_window: int = 60
    rate_limit_suspicious_threshold: int = 5
    monitor_rate_limits: bool = True

    # Security settings
    error_window_minutes: int = 5
    error_threshold: int = 3
