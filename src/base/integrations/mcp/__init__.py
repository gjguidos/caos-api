"""MCP Proxy integration package."""

from src.base.integrations.mcp.client import MCPProxyClient, MCPProxyClientError
from src.base.integrations.mcp.models import (
    MCPProxyHealthResponse,
    MCPToolExecuteResponse,
)

# Note: proxy_tool module is intentionally not imported here to avoid
# circular imports with domains.agentverse. Import it directly when needed:
# from src.base.integrations.mcp.proxy_tool import MCPProxyTool, create_proxy_tool_class

__all__ = [
    # Client
    "MCPProxyClient",
    "MCPProxyClientError",
    # Models
    "MCPProxyHealthResponse",
    "MCPToolExecuteResponse",
]
