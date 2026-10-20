from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.domains.agentverse.cache import helpers as av_cache
from src.domains.agentverse.memory.store import AgentMemoryStore
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry
from src.domains.agentverse.tools.workflow_tool import WorkflowTool
from src.domains.agentverse.tools.workflow_tool.models import WorkflowRun


def test_agent_instance_registry_scopes_agents_by_session() -> None:
    registry = AgentInstanceRegistry()
    first_agent = object()
    second_agent = object()

    registry.set("user-1", "agent-1", first_agent, "session-a")
    registry.set("user-1", "agent-1", second_agent, "session-b")

    assert registry.get("user-1", "agent-1", "session-a") is first_agent
    assert registry.get("user-1", "agent-1", "session-b") is second_agent
    assert registry.count() == 2


@pytest.mark.asyncio
async def test_agent_memory_store_scopes_keys_by_session(monkeypatch: pytest.MonkeyPatch) -> None:
    cache_settings = SimpleNamespace(
        redis_prefix="av:",
        redis_key_ns="test",
        redis_agent_key_multi_tenant=False,
        redis_history_key_multi_tenant=False,
        redis_session_key_multi_tenant=False,
        redis_memory_key_version=1,
        redis_memory_ttl=60,
    )
    monkeypatch.setattr(
        av_cache,
        "get_settings",
        lambda _request: SimpleNamespace(cache=cache_settings),
    )

    repo = _MemoryRepo()
    session_a = AgentMemoryStore(
        repo=repo,
        request=object(),
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-a",
    )
    session_b = AgentMemoryStore(
        repo=repo,
        request=object(),
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-b",
    )

    await session_a.put("active_collection", "collection-a")
    await session_b.put("active_collection", "collection-b")

    assert await session_a.get("active_collection") == "collection-a"
    assert await session_b.get("active_collection") == "collection-b"
    assert any(":session-a:" in key for key in repo.data)
    assert any(":session-b:" in key for key in repo.data)


@pytest.mark.asyncio
async def test_workflow_tool_in_memory_runs_are_instance_local() -> None:
    first_session_tool = WorkflowTool()
    second_session_tool = WorkflowTool()
    run = WorkflowRun(
        run_id="wf_shared_name",
        workflow_name="Example",
        created_at="2026-05-21T00:00:00+00:00",
        updated_at="2026-05-21T00:00:00+00:00",
    )

    first_store = first_session_tool._get_store(context={}, cfg=first_session_tool.config)
    second_store = second_session_tool._get_store(context={}, cfg=second_session_tool.config)

    await first_store.put(run)

    assert await first_store.get("wf_shared_name") == run
    assert await second_store.get("wf_shared_name") is None


def test_workflow_tool_redis_store_prefix_is_session_scoped() -> None:
    tool = WorkflowTool()
    tool.cache = _MemoryRepo()

    first_store = tool._get_store(
        context={"_session_id": "session-a"},
        cfg=tool.config,
    )
    second_store = tool._get_store(
        context={"_session_id": "session-b"},
        cfg=tool.config,
    )

    assert first_store._key("wf_1") != second_store._key("wf_1")
    assert first_store._key("wf_1").startswith("wf:session:session-a:")
    assert second_store._key("wf_1").startswith("wf:session:session-b:")


class _MemoryRepo:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any, expiration: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)
