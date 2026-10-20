"""Regression cover for invalidate_agent.

This previously called `instance_registry()` on an already-resolved instance. The
resulting TypeError was swallowed by a blanket `except Exception`, so eviction silently
did nothing while the caller logged that it had succeeded. Assert it actually evicts.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.cache.helpers import invalidate_agent
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry


def make_request(registry) -> MagicMock:
    request = MagicMock()
    request.app.container = SimpleNamespace(agentverse=SimpleNamespace(instance_registry=registry))
    return request


@pytest.mark.asyncio
async def test_evicts_every_instance_of_the_agent():
    registry = AgentInstanceRegistry()
    target_a, target_b, other = MagicMock(), MagicMock(), MagicMock()
    for agent in (target_a, target_b, other):
        agent.sleep = AsyncMock()

    registry.set("user-1", "agent-1", target_a, "session-1")
    registry.set("user-2", "agent-1", target_b, "session-2")
    registry.set("user-1", "agent-2", other, "session-1")

    request = make_request(registry)
    await invalidate_agent(request, "agent-1")

    target_a.sleep.assert_awaited_once_with(request)
    target_b.sleep.assert_awaited_once_with(request)
    other.sleep.assert_not_awaited()

    assert not registry.exists("user-1", "agent-1", "session-1")
    assert not registry.exists("user-2", "agent-1", "session-2")
    assert registry.exists("user-1", "agent-2", "session-1")


@pytest.mark.asyncio
async def test_a_failing_sleep_still_removes_the_instance():
    registry = AgentInstanceRegistry()
    agent = MagicMock()
    agent.sleep = AsyncMock(side_effect=RuntimeError("redis gone"))
    registry.set("user-1", "agent-1", agent, "session-1")

    await invalidate_agent(make_request(registry), "agent-1")

    assert not registry.exists("user-1", "agent-1", "session-1")
