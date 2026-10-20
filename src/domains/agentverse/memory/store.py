from __future__ import annotations

import json
from typing import Any, Protocol

from src.domains.agentverse.cache import helpers as av_cache


class CacheRepo(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, expiration: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...


class AgentMemoryStore:
    """
    Safe wrapper over av_cache helpers + cache repo.

    Contract:
      - Stores JSON text (utf-8) only.
      - Reads bytes/str and returns decoded Python objects.
      - Fails fast on non-JSON-serialisable values.
    """

    def __init__(
        self,
        *,
        repo: CacheRepo,
        request: Any,
        user_id: str,
        agent_id: str,
        namespace: str = "memory",
        session_id: str | None = None,
    ):
        self.repo = repo
        self.request = request
        self.user_id = user_id
        self.agent_id = agent_id
        self.namespace = (namespace or "memory").strip()
        self.session_id = session_id

    def _key(self, name: str) -> str:
        # namespace gives you room for workflow/run memory later
        full = f"{self.namespace}:{name}"
        return av_cache.memory_key(
            self.request,
            self.user_id,
            self.agent_id,
            full,
            session_id=self.session_id,
        )

    @staticmethod
    def _to_text(raw: Any) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            return raw
        # last resort: some repos might return already-decoded objects
        if isinstance(raw, dict | list | int | float | bool):
            return json.dumps(raw, ensure_ascii=False)
        return str(raw)

    async def put(self, name: str, value: Any, ttl: int | None = None) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except TypeError as e:
            raise TypeError(
                f"AgentMemoryStore.put() value for '{name}' is not JSON-serialisable: {e}"
            ) from e

        effective_ttl = ttl if ttl is not None else av_cache.memory_ttl(self.request)
        await self.repo.set(self._key(name), payload, expiration=effective_ttl)

    async def get(self, name: str, default: Any = None) -> Any:
        raw = await self.repo.get(self._key(name))
        text = self._to_text(raw)
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            return default

    async def delete(self, name: str) -> None:
        await self.repo.delete(self._key(name))
