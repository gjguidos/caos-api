from __future__ import annotations

import json
from typing import Any, Protocol

from src.domains.agentverse.cache import helpers as av_cache


class CacheListRepo(Protocol):
    async def lpush(self, key: str, value: Any) -> Any: ...
    async def ltrim(self, key: str, start: int, stop: int) -> Any: ...
    async def lrange(self, key: str, start: int, stop: int) -> list[Any]: ...
    async def expire(self, key: str, ttl: int) -> Any: ...


class AgentHistoryStore:
    """
    Thin safe wrapper around:
      - av_cache helpers
      - Redis list operations
      - JSON encoding/decoding

    Behavior intentionally identical to current mixin.
    """

    def __init__(self, *, repo: CacheListRepo, request: Any, user_id: str, agent_id: str):
        self.repo = repo
        self.request = request
        self.user_id = user_id
        self.agent_id = agent_id

    def _key(self, session_id: str | None, tool: str | None) -> str:
        return av_cache.history_key(
            self.request,
            self.user_id,
            self.agent_id,
            session_id=session_id,
            tool=tool,
        )

    async def append_many(
        self,
        entries: list[dict],
        *,
        session_id: str | None = None,
        tool: str | None = None,
    ) -> None:
        key = self._key(session_id, tool)

        for e in entries:
            await self.repo.lpush(key, json.dumps(e, ensure_ascii=False))

        max_items = av_cache.history_max_items(self.request)
        await self.repo.ltrim(key, 0, max(0, max_items - 1))

        ttl = av_cache.history_ttl(self.request)
        await self.repo.expire(key, ttl)

    async def fetch(
        self,
        *,
        limit: int = 100,
        session_id: str | None = None,
        tool: str | None = None,
    ) -> list[dict]:
        key = self._key(session_id, tool)
        raw = await self.repo.lrange(key, 0, max(0, limit - 1))

        out: list[dict] = []
        for r in reversed(raw):  # oldest-first
            try:
                out.append(json.loads(r))
            except Exception:
                pass

        return out
