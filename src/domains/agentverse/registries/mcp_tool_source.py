"""Service for loading and registering MCP Proxy tools into the AgentVerse tool registry."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.base.integrations.mcp.client import MCPProxyClient
from src.base.integrations.mcp.proxy_tool import create_proxy_tool_class
from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.utils.canonicalize import canonicalize

if TYPE_CHECKING:
    from src.base.services.file_service import FileService
    from src.domains.agentverse.registries.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPToolSource:
    """
    Tool source that loads tools from MCP Proxy server and registers them
    with the AgentVerse tool registry.

    This acts as a bridge between remote MCP tools and the local tool system.
    """

    def __init__(
        self,
        mcp_client: MCPProxyClient,
        tool_registry: ToolRegistry,
        file_service: FileService | None = None,
    ):
        self.mcp_client = mcp_client
        self.tool_registry = tool_registry
        self._file_service = file_service
        self._registered_tools: dict[str, type] = {}
        self._loaded = False

    @property
    def is_enabled(self) -> bool:
        """Check if MCP tool source is enabled."""
        return self.mcp_client.is_enabled

    async def load_tools(self, force_refresh: bool = False) -> int:
        if not self.is_enabled:
            logger.debug("MCP Proxy is disabled, skipping tool loading")
            return 0

        if self._loaded and not force_refresh:
            logger.debug("MCP tools already loaded, use force_refresh=True to reload")
            return len(self._registered_tools)

        try:
            specs = await self.mcp_client.list_tools()

            if not specs:
                logger.info("No tools available from MCP Proxy")
                self._loaded = True
                return 0

            registered_count = 0
            self._registered_tools.clear()

            for spec in specs:
                try:
                    self._register_tool(spec)
                    registered_count += 1
                except Exception as e:
                    logger.warning(f"Failed to register MCP tool '{spec.name}': {e}")

            self._loaded = True
            logger.info(f"Loaded {registered_count} tools from MCP Proxy")
            return registered_count

        except Exception as e:
            logger.error(f"Failed to load tools from MCP Proxy: {e}", exc_info=True)
            return 0

    def _register_tool(self, spec: ToolSpec) -> None:
        """Register a single MCP tool with the tool registry using its ToolSpec."""
        # Remote names are outside our control, so this is the one registration path that
        # coerces rather than validates: canonicalize explicitly here, at the boundary.
        tool_key = canonicalize(spec.key or re.sub(r"\s+tool$", "", spec.name, flags=re.IGNORECASE))

        # Determine route based on execution_mode from proxy metadata
        raw_mode = spec.metadata.get("execution_mode") or spec.metadata.get("route") or "pure"

        if raw_mode in ("context_only", "invoker", "llm"):
            route = "invoker"
            llm_free = False
        else:
            route = "pure"
            llm_free = True

        # Enrich metadata with MCP-specific fields
        enriched_metadata = {
            **spec.metadata,
            "supports": dict(spec.supports or {}),
            "source": "mcp_proxy",
            "remote_tool_key": tool_key,
            "remote_tool_name": spec.name,
            "is_remote": True,
            "route": route,
            "llm_free": llm_free,
        }

        # Create proxy tool class and register
        tool_class = create_proxy_tool_class(spec, self.mcp_client, self._file_service)

        self.tool_registry.register_tool(
            key=tool_key,
            name=spec.name or f"MCP: {tool_key}",
            description=spec.description or f"Remote MCP tool: {tool_key}",
            version=spec.version,
            metadata=enriched_metadata,
            component=tool_class,
        )

        self._registered_tools[tool_key] = tool_class
        logger.info(f"Registered MCP tool: {spec.name} (key={tool_key})")

    async def refresh_tools(self) -> int:
        await self._remove_registered_tools()
        self._registered_tools.clear()
        self._loaded = False
        return await self.load_tools(force_refresh=True)

    def get_registered_tools(self) -> list[str]:
        """Get list of registered MCP tool names."""
        return list(self._registered_tools.keys())

    async def unload_tools(self) -> None:
        await self._remove_registered_tools()
        self._registered_tools.clear()
        self._loaded = False
        logger.info("MCP tools unloaded")

    async def _remove_registered_tools(self) -> None:
        for key in list(self._registered_tools.keys()):
            try:
                self.tool_registry.unregister(key)
                logger.info(f"Removed MCP tool from registry: {key}")
            except Exception as e:
                logger.warning(f"Failed to remove MCP tool '{key}' from registry: {e}")
