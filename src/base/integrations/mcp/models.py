"""Pydantic models for MCP Proxy API responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MCPToolExecuteResponse(BaseModel):
    """Response from tool execution."""

    success: bool = Field(..., description="Whether execution succeeded")
    result: Any = Field(None, description="Tool execution result")
    error: str | None = Field(None, description="Error message if failed")
    meta: dict[str, Any] | None = Field(None, description="Execution metadata")


class MCPProxyHealthResponse(BaseModel):
    """Health check response from MCP Proxy."""

    status: str = Field(..., description="Health status")
    version: str | None = Field(None, description="Proxy version")
    uptime_seconds: float | None = Field(None, description="Server uptime")
    connected_servers: int | None = Field(
        None, description="Tool servers the proxy is connected to"
    )
