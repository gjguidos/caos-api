from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapTopology,
)


class SitemapResourceState(BaseModel):
    url: str
    final_url: str
    acquired_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    topology: SitemapTopology


class SitemapStateStore:
    """
    Ephemeral sitemap state for one crawl operation.

    This state is intentionally not shared between users, sessions, or runs.
    """

    def __init__(self) -> None:
        self._states: dict[str, SitemapResourceState] = {}

    @staticmethod
    def _normalize_url(url: str) -> str:
        parts = urlsplit(url.strip())

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path or "/",
                parts.query,
                "",
            )
        )

    async def get(
        self,
        url: str,
    ) -> SitemapResourceState | None:
        key = self._normalize_url(url)
        state = self._states.get(key)

        if state is None:
            return None

        return state.model_copy(deep=True)

    async def put(
        self,
        state: SitemapResourceState,
    ) -> None:
        key = self._normalize_url(state.url)

        self._states[key] = state.model_copy(
            deep=True,
        )

    async def delete(
        self,
        url: str,
    ) -> None:
        self._states.pop(
            self._normalize_url(url),
            None,
        )

    async def clear(self) -> None:
        self._states.clear()