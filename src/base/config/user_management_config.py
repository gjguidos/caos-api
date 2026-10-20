from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class UserManagementConfig(BaseSettings):
    """Configuration for the User Management microservice integration."""

    authz_enabled: bool = Field(
        default=True,
        description="Enable authorization checks via the user management service. "
        "Set to false to skip all permission checks.",
    )
    authz_url: str = Field(
        default="",
        description="Base URL of the CAOS-user-management-api service",
    )
    authz_cache_ttl: int = Field(
        default=60,
        description="TTL in seconds for the in-process authorisation check cache",
    )
    authz_timeout: float = Field(
        default=10.0,
        description="HTTP timeout in seconds for /authz/check calls",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
