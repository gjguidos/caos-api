from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.domains.agentverse.tools.spec import ToolSpec

from pydantic import BaseModel

from src.base.config.mcp_proxy_config import MCPProxyConfig
from src.base.infrastructure.http import AsyncHttpClient, HttpClientConfig
from src.base.integrations.mcp.models import MCPProxyHealthResponse, MCPToolExecuteResponse

logger = logging.getLogger(__name__)


class MCPProxyClientError(Exception):
    """Base exception for MCP Proxy client errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump(mode="json")
        except Exception:
            pass

    if hasattr(value, "dict") and callable(value.dict):
        try:
            return value.dict()
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return {str(k): _json_safe(v) for k, v in vars(value).items() if not k.startswith("_")}
        except Exception:
            pass

    return str(value)


class MCPProxyClient:
    """
    Async HTTP client for communicating with MCP Proxy server.

    Provides methods to:
    - Fetch available tools
    - Execute tools remotely
    - Check proxy health
    """

    def __init__(self, config: MCPProxyConfig, http_client: AsyncHttpClient | None = None):
        self.config = config
        self._http_client = http_client
        self._enabled = config.mcp_proxy_enabled

    @property
    def http_client(self) -> AsyncHttpClient:
        """Lazy-initialize HTTP client if not provided."""
        if self._http_client is None:
            http_config = HttpClientConfig(
                base_url=self.config.mcp_proxy_base_url,
                timeout=self.config.mcp_proxy_timeout,
                max_retries=self.config.mcp_proxy_max_retries,
                headers={"Content-Type": "application/json"},
            )
            self._http_client = AsyncHttpClient(config=http_config)
        return self._http_client

    @property
    def is_enabled(self) -> bool:
        """Check if MCP proxy integration is enabled."""
        return self._enabled

    async def health_check(self) -> MCPProxyHealthResponse:
        """
        Check MCP Proxy server health.

        Returns:
            MCPProxyHealthResponse with status information

        Raises:
            MCPProxyClientError: If health check fails
        """
        if not self._enabled:
            return MCPProxyHealthResponse(status="disabled")

        try:
            # `mcp_proxy_base_url` already ends in `/api`, where the proxy mounts
            # its readiness endpoint. Its `/health` route is mounted at the root
            # and is therefore unreachable from this client.
            response = await self.http_client.get("/ready")
            return MCPProxyHealthResponse(**response)
        except Exception as e:
            logger.warning(f"MCP Proxy health check failed: {e}")
            raise MCPProxyClientError(f"Health check failed: {e}") from e

    async def list_tools(self) -> list[ToolSpec]:
        """
        Fetch list of available tools from MCP Proxy.

        Returns:
            List of ToolSpec definitions

        Raises:
            MCPProxyClientError: If request fails
        """
        if not self._enabled:
            return []

        try:
            response = await self.http_client.get("/tools")
            tools_data = response.get("tools", response)

            if isinstance(tools_data, list):
                tools = [self._parse_tool_spec(tool_data) for tool_data in tools_data]
                logger.info(f"Loaded {len(tools)} tools from MCP Proxy")
                return tools
            else:
                logger.warning(f"Unexpected response format from /tools: {type(tools_data)}")
                return []

        except Exception as e:
            logger.error(f"Failed to list tools from MCP Proxy: {e}")
            raise MCPProxyClientError(f"Failed to list tools: {e}") from e

    def _parse_tool_spec(self, data: dict[str, Any]) -> ToolSpec:
        """Parse raw tool data from MCP Proxy into a ToolSpec."""
        from src.domains.agentverse.tools.spec import ToolSpec

        tool_key = data.get("key") or data.get("name", "unknown")
        tool_name = data.get("name", tool_key)

        return ToolSpec(
            key=tool_key,
            name=tool_name,
            description=data.get("description"),
            version=data.get("version", "1.0.0"),
            tags=data.get("tags", []),
            source="mcp_proxy",
            metadata=data.get("metadata", {}),
            config=data.get("config", {}),
            supports=data.get("supports", {}),
        )

    async def get_tool(self, tool_name: str) -> ToolSpec | None:
        """
        Get a specific tool definition by name.

        Args:
            tool_name: Name of the tool to retrieve

        Returns:
            ToolSpec or None if not found
        """
        if not self._enabled:
            return None

        tools = await self.list_tools()
        return next((t for t in tools if t.key == tool_name), None)

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        method: str | None = None,
    ) -> MCPToolExecuteResponse:
        if not self._enabled:
            return MCPToolExecuteResponse(
                success=False,
                error="MCP Proxy integration is disabled",
            )

        resolved_method = (method or "execute").strip()

        try:
            safe_arguments = _json_safe(arguments or {})
            payload: dict[str, Any] = safe_arguments

            logger.debug(
                "Executing MCP proxy tool '%s' with method=%r param_keys=%s",
                tool_name,
                resolved_method,
                sorted((safe_arguments or {}).keys()),
            )

            response = await self.http_client.post(
                f"/tools/{tool_name}/{resolved_method}/execute",
                json=payload,
            )

            return MCPToolExecuteResponse(
                success=response.get("success", True),
                result=response.get("result"),
                error=response.get("error"),
                meta=response.get("meta"),
            )

        except Exception as e:
            logger.error(
                "Failed to execute tool '%s' with method=%r: %s",
                tool_name,
                resolved_method,
                e,
            )
            return MCPToolExecuteResponse(
                success=False,
                error=str(e),
            )

    async def get_servers(self) -> list[dict[str, Any]]:
        """
        Get list of connected tool servers.

        Returns:
            List of server information dictionaries
        """
        if not self._enabled:
            return []

        try:
            response = await self.http_client.get("/servers")
            return response.get("servers", [])
        except Exception as e:
            logger.warning(f"Failed to get servers from MCP Proxy: {e}")
            return []
