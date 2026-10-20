"""Terminal failure-event contract for joshu-a.create (Part 1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.services.divine_orchestration_service import (
    DivineOrchestrationService,
)


def _service(agent_service_create_side_effect=None) -> DivineOrchestrationService:
    agent_service = MagicMock()
    agent_service.create_agent = AsyncMock(side_effect=agent_service_create_side_effect)
    agent_chat_service = MagicMock()
    return DivineOrchestrationService(agent_service, agent_chat_service)


def _emitter() -> MagicMock:
    emitter = MagicMock()
    emitter.log = AsyncMock()
    emitter.event = AsyncMock()
    return emitter


def _websocket(user) -> MagicMock:
    ws = MagicMock()
    ws.scope = {"user": user}
    ws.app = SimpleNamespace()
    return ws


def _terminal_events(emitter: MagicMock) -> list[dict]:
    return [
        call.args[1]["response"]
        for call in emitter.event.call_args_list
        if call.args and call.args[0] == "joshu-a.create"
    ]


@pytest.mark.asyncio
async def test_unauthenticated_emits_error_terminal_event():
    svc = _service()
    emitter = _emitter()

    result = await svc.create_agent(
        websocket=_websocket(user=None),
        socket_emitter=emitter,
        socket_id="sess-1",
    )

    assert result == {"error": "User not authenticated"}
    events = _terminal_events(emitter)
    assert len(events) == 1
    payload = events[0]
    assert payload["status"] == "ERROR"
    assert payload["agent"] is None
    assert payload["response"] == "User not authenticated"


@pytest.mark.asyncio
async def test_parse_failure_emits_error_terminal_event():
    svc = _service()
    emitter = _emitter()
    # No "message" payload -> AgentRequest construction fails during parse.
    result = await svc.create_agent(
        websocket=_websocket(user=SimpleNamespace(id="u1")),
        socket_emitter=emitter,
        socket_id="sess-1",
        message="not-a-dict",
    )

    assert "error" in result
    events = _terminal_events(emitter)
    assert len(events) == 1
    assert events[0]["status"] == "ERROR"
    assert events[0]["agent"] is None
    assert events[0]["response"]


@pytest.mark.asyncio
async def test_success_terminal_event_shape_unchanged():
    agent = SimpleNamespace(id="agent-123", system_name="my_new_agent", name="My new agent")
    svc = _service()
    svc.agent_service.create_agent = AsyncMock(return_value=agent)
    svc.agent_chat_service.test_agent = AsyncMock(return_value={"status": "success"})

    # Bypass request parsing so we exercise the success finalize path directly.
    svc._parse_request = AsyncMock(return_value=SimpleNamespace(agent_type=None, tools=[]))

    emitter = _emitter()
    result = await svc.create_agent(
        websocket=_websocket(user=SimpleNamespace(id="u1")),
        socket_emitter=emitter,
        socket_id="sess-1",
        message={},
    )

    assert result == {"agent_id": "agent-123", "agent_system_name": "my_new_agent"}
    events = _terminal_events(emitter)
    assert len(events) == 1
    payload = events[0]
    assert payload["status"] == "OK"
    assert payload["agent"]["agent_id"] == "agent-123"
    assert "agent" in payload and "response" in payload and "status" in payload
