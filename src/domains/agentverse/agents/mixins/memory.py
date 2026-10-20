from __future__ import annotations

from typing import Any

from src.domains.agentverse.memory.store import AgentMemoryStore


class MemoryMixin:
    # ---------- base store ----------
    def _memory_store(self, *, namespace: str = "memory") -> AgentMemoryStore | None:
        repo = getattr(self, "cache", None)
        if repo is None:
            return None

        req = getattr(self, "request", None)
        # If your memory_key depends on request, fail closed rather than writing to a bad key.
        if req is None:
            return None

        user_id = getattr(self, "user_id", None)
        agent_id = getattr(self, "id", None)
        if not user_id or not agent_id:
            return None

        return AgentMemoryStore(
            repo=repo,
            request=req,
            user_id=str(user_id),
            agent_id=str(agent_id),
            namespace=namespace,
            session_id=getattr(self, "default_session_id", None),
        )

    async def remember(self, name: str, value: Any, ttl: int | None = None) -> None:
        store = self._memory_store(namespace="memory")
        if not store:
            return
        await store.put(name, value, ttl=ttl)

    async def recall(self, name: str, default: Any = None) -> Any:
        store = self._memory_store(namespace="memory")
        if not store:
            return default
        return await store.get(name, default=default)

    async def forget(self, name: str) -> None:
        store = self._memory_store(namespace="memory")
        if not store:
            return
        await store.delete(name)

    # ---------- workflow/run-scoped store ----------
    def workflow_store(self, *, run_id: str) -> AgentMemoryStore | None:
        """
        Store for deterministic workflow state, isolated from general agent memory.
        Keys become: memory_key(..., f"workflow:run:{run_id}:<name>")
        """
        run_id = (run_id or "").strip()
        if not run_id:
            return None
        return self._memory_store(namespace=f"workflow:run:{run_id}")

    async def remember_workflow(
        self, run_id: str, name: str, value: Any, ttl: int | None = None
    ) -> None:
        store = self.workflow_store(run_id=run_id)
        if not store:
            return
        await store.put(name, value, ttl=ttl)

    async def recall_workflow(self, run_id: str, name: str, default: Any = None) -> Any:
        store = self.workflow_store(run_id=run_id)
        if not store:
            return default
        return await store.get(name, default=default)

    async def forget_workflow(self, run_id: str, name: str) -> None:
        store = self.workflow_store(run_id=run_id)
        if not store:
            return
        await store.delete(name)
