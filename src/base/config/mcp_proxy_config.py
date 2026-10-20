"""Configuration for MCP Proxy integration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class MCPProxyConfig(BaseSettings):
    """Configuration for connecting to MCP Proxy server."""

    mcp_proxy_enabled: bool = Field(
        default=False,
        description="Enable MCP Proxy integration for remote tools",
    )
    mcp_proxy_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL for the MCP Proxy server",
    )
    mcp_proxy_timeout: float = Field(
        default=60.0,
        description="Timeout for MCP Proxy API requests in seconds",
    )
    mcp_proxy_max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for MCP Proxy calls",
    )
    mcp_proxy_health_check_interval: float = Field(
        default=30.0,
        description="Interval for health check polling in seconds",
    )
    mcp_proxy_tool_cache_ttl: int = Field(
        default=300,
        description="Time-to-live for cached tool definitions in seconds",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
