"""Teardown guarantees for the websocket session runner.

Every resource acquired in the session must be released no matter how far setup got.
These paths regress silently, so they are pinned here rather than left to review.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from src.domains.agentverse.interfaces.api.v1.websockets import routes
from src.domains.agentverse.interfaces.api.v1.websockets.routes import (
    ws_chat_handler,
    ws_handler,
)


@pytest.fixture
def commbridge() -> MagicMock:
    bridge = MagicMock()
    bridge.connect = AsyncMock()
    bridge.disconnect = AsyncMock()
    return bridge


@pytest.fixture
def websocket(commbridge) -> MagicMock:
    ws = MagicMock()
    ws.scope = {}
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app.state.cognitive_modules = {"communication": {"socketRedisBridge": commbridge}}
    return ws


@pytest.fixture(autouse=True)
def no_message_handlers(monkeypatch):
    monkeypatch.setattr(routes, "register_message_events", MagicMock())


# Both routes are wrapped by session_based_ws; reach the undecorated bodies directly so
# these tests cover teardown rather than re-testing authentication.
chat_handler = ws_chat_handler.__wrapped__
joshua_handler = ws_handler.__wrapped__


@pytest.mark.asyncio
async def test_disconnect_before_first_message_still_cleans_up(websocket, commbridge):
    """The leak this route's restructuring fixes: no first frame, but full teardown."""
    websocket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

    await chat_handler(websocket, "joshu-a")

    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_bridge_disconnects_when_the_first_frame_is_malformed(websocket, commbridge):
    """A crash during setup must not skip cleanup either."""
    websocket.receive_json = AsyncMock(side_effect=ValueError("not json"))

    with pytest.raises(ValueError):
        await chat_handler(websocket, "joshu-a")

    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_bridge_is_never_disconnected_without_being_connected(websocket, commbridge):
    """disconnect() closes the socket, so it must only ever follow a real connect()."""
    commbridge.connect = AsyncMock(side_effect=RuntimeError("redis down"))

    with pytest.raises(RuntimeError):
        await chat_handler(websocket, "joshu-a")

    commbridge.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_presence_is_entered_even_without_user_and_agent(monkeypatch, websocket):
    """presence_session owns the no-op case, so the session never branches on it."""
    seen = {}

    @asynccontextmanager
    async def fake_session(socket, user_id, agent_id, session_id):
        seen["identity"] = (user_id, agent_id)
        yield

    monkeypatch.setattr(routes.presence, "presence_session", fake_session)
    websocket.receive_json = AsyncMock(
        side_effect=[{"event": "chat.send", "data": {}}, WebSocketDisconnect()]
    )

    await chat_handler(websocket, "joshu-a")

    assert seen["identity"] == (None, None)


@pytest.mark.asyncio
async def test_presence_released_when_the_session_ends(monkeypatch, websocket, commbridge):
    released = []

    class _Session:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            released.append(True)
            return False

    monkeypatch.setattr(routes.presence, "presence_session", lambda *a: _Session())

    websocket.receive_json = AsyncMock(
        side_effect=[
            {"event": "chat.send", "user_id": "u1", "agent_id": "a1", "data": {}},
            WebSocketDisconnect(),
        ]
    )

    await chat_handler(websocket, "joshu-a")

    assert released == [True]
    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_presence_is_released_after_handlers_are_cancelled(monkeypatch, websocket):
    """Nothing may still be touching the agent when presence puts it to sleep."""
    order = []
    started = asyncio.Event()

    class _Session:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            order.append("presence_released")
            return False

    async def never_finishes(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("handler_cancelled")
            raise

    monkeypatch.setattr(routes.presence, "presence_session", lambda *a: _Session())
    monkeypatch.setattr(routes, "_dispatch", never_finishes)

    async def receive():
        if not websocket.receive_json.sent_first:
            websocket.receive_json.sent_first = True
            return {"event": "chat.send", "user_id": "u1", "agent_id": "a1", "data": {}}
        await started.wait()
        raise WebSocketDisconnect()

    websocket.receive_json = AsyncMock(side_effect=receive)
    websocket.receive_json.sent_first = False

    await chat_handler(websocket, "joshu-a")

    assert order == ["handler_cancelled", "presence_released"]


@pytest.mark.asyncio
async def test_in_flight_handlers_are_cancelled(monkeypatch, websocket):
    """No handler task may outlive the socket that spawned it."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_finishes(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(routes, "_dispatch", never_finishes)

    async def receive():
        if not websocket.receive_json.sent_first:
            websocket.receive_json.sent_first = True
            return {"event": "chat.send", "data": {}}
        await started.wait()
        raise WebSocketDisconnect()

    websocket.receive_json = AsyncMock(side_effect=receive)
    websocket.receive_json.sent_first = False

    await chat_handler(websocket, "joshu-a")

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_joshua_dispatches_sequentially(monkeypatch, websocket, commbridge):
    """Joshu-A frames mutate agents, so each must finish before the next one starts."""
    overlapping = []
    running = 0

    async def slow_dispatch(_router, _emitter, event, _data):
        nonlocal running
        running += 1
        overlapping.append(running)
        await asyncio.sleep(0)
        running -= 1

    monkeypatch.setattr(routes, "_dispatch", slow_dispatch)
    websocket.receive_json = AsyncMock(
        side_effect=[
            {"event": "joshu-a.create", "data": {}},
            {"event": "joshu-a.comm", "data": {}},
            WebSocketDisconnect(),
        ]
    )

    await joshua_handler(websocket)

    assert overlapping == [1, 1]
    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_every_frame_is_dispatched_including_the_first(monkeypatch, websocket):
    """The first frame is a message like any other, not a special setup case."""
    dispatched = []

    async def record(_router, _emitter, event, data):
        dispatched.append((event, data["_session_id"]))

    monkeypatch.setattr(routes, "_dispatch", record)
    websocket.receive_json = AsyncMock(
        side_effect=[
            {"event": "Joshu-A.Create", "data": {}},
            {"event": "joshu-a.comm", "data": {}},
            WebSocketDisconnect(),
        ]
    )

    await joshua_handler(websocket)

    events = [event for event, _ in dispatched]
    session_ids = {session_id for _, session_id in dispatched}
    assert events == ["joshu-a.create", "joshu-a.comm"]
    assert len(session_ids) == 1 and None not in session_ids


@pytest.mark.parametrize(
    ("first_message", "path_name", "expected"),
    [
        ({"event": "chat.send", "user_id": "u1", "agent_id": "a1"}, "eva", ("chat", "u1", "a1")),
        ({"event": "joshu-a.create"}, "joshu-a", ("joshu-a", None, None)),
        ({"event": "", "agent_system_name": "EVA"}, "fallback", ("eva", None, None)),
        ({}, "FromPath", ("frompath", None, None)),
    ],
)
def test_identity_is_negotiated_from_the_first_frame(first_message, path_name, expected):
    identity = routes._identify(first_message, path_name)

    assert (identity.agent_system_name, identity.user_id, identity.agent_id) == expected
