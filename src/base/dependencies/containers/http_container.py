from __future__ import annotations

from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.infrastructure.http.async_http_client import AsyncHttpClient
from src.base.infrastructure.http.http_client_config import HttpClientConfig

# Import directly from client module to avoid circular import via __init__.py
from src.base.integrations.mcp.client import MCPProxyClient
from src.base.repositories.http_repository import HttpRepository


class HttpContainer(containers.DeclarativeContainer):
    """Dependency injection container for HTTP clients."""

    config = providers.Configuration()

    # ------------------------------------------------------------
    # Default generic HTTP client
    # ------------------------------------------------------------

    default_http_config = providers.Singleton(
        HttpClientConfig,
        base_url=None,
        timeout=30,
        max_retries=2,
        headers={"User-Agent": "AgentVerse/1.0"},
        follow_redirects=True,
        verify_ssl=True,
    )

    default_http_client = providers.Singleton(
        AsyncHttpClient,
        config=default_http_config,
    )

    default_http_repository = providers.Singleton(
        HttpRepository,
        client=default_http_client,
    )

    # ------------------------------------------------------------
    # Existing specific clients
    # ------------------------------------------------------------

    mcp_proxy_http_config = providers.Singleton(
        HttpClientConfig,
        base_url=settings.mcp_proxy.mcp_proxy_base_url,
        timeout=settings.mcp_proxy.mcp_proxy_timeout,
        max_retries=settings.mcp_proxy.mcp_proxy_max_retries,
        headers={"Content-Type": "application/json"},
        follow_redirects=True,
        verify_ssl=True,
    )

    mcp_proxy_http_client = providers.Singleton(
        AsyncHttpClient,
        config=mcp_proxy_http_config,
    )

    mcp_proxy_client = providers.Singleton(
        MCPProxyClient,
        config=providers.Object(settings.mcp_proxy),
        http_client=mcp_proxy_http_client,
    )