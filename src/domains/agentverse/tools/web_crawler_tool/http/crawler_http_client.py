from __future__ import annotations

from typing import Any

import httpx


class CrawlerHttpClient:
    """Pooled HTTP transport owned by one crawler operation."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        user_agent: str,
        max_connections: int,
        max_keepalive_connections: int | None = None,
    ) -> None:
        if max_connections < 1:
            raise ValueError(
                "max_connections must be greater than zero"
            )

        keepalive_connections = (
            max_keepalive_connections
            if max_keepalive_connections is not None
            else max_connections
        )

        if keepalive_connections < 1:
            raise ValueError(
                "max_keepalive_connections must be greater than zero"
            )

        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=keepalive_connections,
            ),
        )

    async def get(
        self,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        return await self._client.get(
            url,
            **kwargs,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CrawlerHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        await self.aclose()
