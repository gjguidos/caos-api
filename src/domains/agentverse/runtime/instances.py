import asyncio
from collections import defaultdict
from collections.abc import Iterable


class AgentInstanceRegistry:
    def __init__(self):
        # (user_id, agent_id, session_id) -> agent instance
        self._map: dict[tuple[str, str, str], object] = {}
        # one local asyncio lock per (user, agent, session)
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _k(
        self, user_id: str, agent_id: str, session_id: str | None = None
    ) -> tuple[str, str, str]:
        return (user_id, agent_id, session_id or "default")

    # ---------- core ----------
    def get(self, user_id: str, agent_id: str, session_id: str | None = None):
        return self._map.get(self._k(user_id, agent_id, session_id))

    def set(self, user_id: str, agent_id: str, agent, session_id: str | None = None) -> None:
        self._map[self._k(user_id, agent_id, session_id)] = agent

    def remove(self, user_id: str, agent_id: str, session_id: str | None = None) -> None:
        key = self._k(user_id, agent_id, session_id)
        self._map.pop(key, None)
        # optional: free the per-key lock to avoid unbounded growth
        self._locks.pop(key, None)

    def lock(self, user_id: str, agent_id: str, session_id: str | None = None) -> asyncio.Lock:
        return self._locks[self._k(user_id, agent_id, session_id)]

    # ---------- conveniences (nice for admin/debug) ----------
    def exists(self, user_id: str, agent_id: str, session_id: str | None = None) -> bool:
        return self._k(user_id, agent_id, session_id) in self._map

    def keys(self) -> Iterable[tuple[str, str, str]]:
        return tuple(self._map.keys())

    def values(self) -> Iterable[object]:
        return tuple(self._map.values())

    def items(self) -> Iterable[tuple[tuple[str, str, str], object]]:
        return tuple(self._map.items())

    def list_user(self, user_id: str) -> Iterable[str]:
        return tuple(aid for (uid, aid, _sid) in self._map.keys() if uid == user_id)

    def count(self) -> int:
        return len(self._map)
