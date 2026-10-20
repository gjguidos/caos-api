from __future__ import annotations

from pydantic import BaseModel, Field


class HttpClientConfig(BaseModel):
    """Configuration for the async HTTP client."""

    base_url: str | None = Field(default=None, description="Base URL for API requests")
    timeout: float = Field(default=30.0, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum number of retry attempts")
    retry_backoff: float = Field(default=0.5, description="Backoff multiplier for retries")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Default headers for all requests"
    )
    follow_redirects: bool = Field(default=True, description="Whether to follow HTTP redirects")
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")
