"""The proxy's readiness endpoint is the one reachable from this client.

`mcp_proxy_base_url` ends in `/api`; the proxy mounts `/health` at the root and
`/ready` on the api router, so a `/health` request resolves to `/api/health` and
404s.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.base.config.mcp_proxy_config import MCPProxyConfig
from src.base.integrations.mcp.client import MCPProxyClient


class _FakeHttpClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.paths: list[str] = []

    async def get(self, url: str, **_: Any) -> dict[str, Any]:
        self.paths.append(url)
        return self._response


def _client(response: dict[str, Any]) -> tuple[MCPProxyClient, _FakeHttpClient]:
    http = _FakeHttpClient(response)
    config = MCPProxyConfig(mcp_proxy_enabled=True)
    return MCPProxyClient(config=config, http_client=http), http  # type: ignore[arg-type]


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_it_requests_the_readiness_path(self) -> None:
        client, http = _client({"status": "ready", "connected_servers": 2})

        await client.health_check()

        assert http.paths == ["/ready"]

    @pytest.mark.asyncio
    async def test_the_readiness_payload_parses(self) -> None:
        client, _ = _client({"status": "ready", "connected_servers": 2})

        response = await client.health_check()

        assert response.status == "ready"
        assert response.connected_servers == 2

    @pytest.mark.asyncio
    async def test_disabled_proxy_makes_no_request(self) -> None:
        http = _FakeHttpClient({})
        client = MCPProxyClient(
            config=MCPProxyConfig(mcp_proxy_enabled=False),
            http_client=http,  # type: ignore[arg-type]
        )

        assert (await client.health_check()).status == "disabled"
        assert http.paths == []
