from __future__ import annotations

from src.domains.agentverse.history.store import AgentHistoryStore


class HistoryMixin:
    def _history_store(self) -> AgentHistoryStore | None:
        repo = getattr(self, "cache", None)
        if not repo:
            return None
        req = getattr(self, "request", None)
        return AgentHistoryStore(
            repo=repo,
            request=req,
            user_id=self.user_id,
            agent_id=self.id,
        )

    async def append_entry(
        self,
        role: str,
        content: str,
        *,
        session_id: str | None = None,
        tool: str | None = None,
        name: str | None = None,
        meta: dict | None = None,
    ):
        store = self._history_store()
        if not store:
            return

        entry = {
            "role": role,
            "content": content,
            **({"name": name} if name else {}),
            **({"meta": meta} if meta else {}),
        }

        await store.append_many([entry], session_id=session_id, tool=tool)

    async def get_memory_history(
        self,
        limit: int = 100,
        session_id: str | None = None,
        tool: str | None = None,
    ) -> list[dict]:
        store = self._history_store()
        if not store:
            return []

        return await store.fetch(limit=limit, session_id=session_id, tool=tool)
