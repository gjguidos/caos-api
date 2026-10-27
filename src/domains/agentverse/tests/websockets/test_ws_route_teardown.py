"""Exercise route lifecycles with authentication's established scope contract."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import WebSocketDisconnect

from src.base.events.channels import user_event_channel
from src.base.middlewares.request_context import request_context
from src.domains.agentverse.interfaces.api.v1.websockets import routes
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry

# Authentication itself is tested separately. These bodies receive the same
# trusted scope identity that session_based_ws supplies, never a payload identity.
chat_handler = routes.ws_chat_handler.__wrapped__
joshua_handler = routes.ws_handler.__wrapped__


@pytest.fixture
def commbridge():
    return SimpleNamespace(connect=AsyncMock(), subscribe=AsyncMock(), disconnect=AsyncMock())


@pytest.fixture
def websocket(commbridge):
    ws = Mock()
    ws.scope = {
        "user": SimpleNamespace(id="trusted-user", roles=["user"]),
        "tenant_id": "trusted-tenant",
    }
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app = SimpleNamespace(
        state=SimpleNamespace(
            cognitive_modules={"communication": {"socketRedisBridge": commbridge}}
        ),
        container=SimpleNamespace(
            agentverse=SimpleNamespace(instance_registry=AgentInstanceRegistry())
        ),
    )
    return ws


@pytest.fixture
def dispatch():
    return AsyncMock(return_value={"ok": True})


@pytest.fixture(autouse=True)
def session_dependencies(monkeypatch, dispatch):
    contexts = []

    def register(event_router, ctx):
        contexts.append(ctx)
        for event in ("chat.send", "joshu-a.create", "joshu-a.comm", ""):
            event_router.register(event, dispatch)

    monkeypatch.setattr(routes, "register_message_events", Mock(side_effect=register))
    for name in ("presence_join", "presence_heartbeat", "touch_spawn"):
        monkeypatch.setattr(routes.av_cache, name, AsyncMock())
    # Another socket remains by default, so cleanup does not need Redis/agent IO.
    monkeypatch.setattr(routes.av_cache, "presence_leave", AsyncMock(return_value=1))

    async def heartbeat(request, user_id, agent_id, session_id, stop):
        await stop.wait()

    monkeypatch.setattr(routes, "_presence_heartbeat", heartbeat)
    return contexts


def feed_frames(websocket, *frames):
    messages = iter(frames)

    async def receive():
        # Let scheduled handlers start; no dependency on wall-clock sleeps.
        await asyncio.sleep(0)
        message = next(messages, WebSocketDisconnect())
        if isinstance(message, BaseException):
            raise message
        return message

    websocket.receive_json = AsyncMock(side_effect=receive)


def disconnect_after_dispatch_starts(websocket, started, *, agent_id=None):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"event": "chat.send", "agent_id": agent_id, "data": {}}
        await asyncio.wait_for(started.wait(), timeout=1)
        raise WebSocketDisconnect()

    websocket.receive_json = AsyncMock(side_effect=receive)


@pytest.mark.asyncio
async def test_disconnect_before_first_message_still_cleans_up(websocket, commbridge):
    feed_frames(websocket)

    await chat_handler(websocket, "joshu-a")

    session_id = commbridge.connect.await_args.args[0]
    commbridge.disconnect.assert_awaited_once_with(session_id)
    assert websocket not in routes.connected_clients
    routes.av_cache.presence_join.assert_not_awaited()
    routes.av_cache.presence_leave.assert_not_awaited()


@pytest.mark.asyncio
async def test_bridge_disconnects_when_the_first_frame_is_malformed(websocket, commbridge):
    feed_frames(websocket, ValueError("not json"))

    with pytest.raises(ValueError, match="not json"):
        await chat_handler(websocket, "joshu-a")

    commbridge.disconnect.assert_awaited_once_with(commbridge.connect.await_args.args[0])
    assert websocket not in routes.connected_clients


@pytest.mark.asyncio
async def test_the_bridge_is_never_disconnected_without_being_connected(websocket, commbridge):
    commbridge.connect.side_effect = RuntimeError("redis down")

    with pytest.raises(RuntimeError, match="redis down"):
        await chat_handler(websocket, "joshu-a")

    commbridge.connect.assert_awaited_once()
    commbridge.subscribe.assert_not_awaited()
    commbridge.disconnect.assert_not_awaited()
    assert websocket not in routes.connected_clients


@pytest.mark.asyncio
async def test_authenticated_session_without_agent_does_not_acquire_presence(
    websocket, commbridge, dispatch, session_dependencies
):
    """Replace the obsolete anonymous presence-helper assumption with real behavior."""
    feed_frames(websocket, {"event": "chat.send", "data": {}})

    await chat_handler(websocket, "joshu-a")

    assert session_dependencies[0].user_id == "trusted-user"
    assert session_dependencies[0].agent_id is None
    dispatch.assert_awaited_once()
    routes.av_cache.presence_join.assert_not_awaited()
    routes.av_cache.presence_leave.assert_not_awaited()
    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_presence_released_when_the_session_ends(websocket, commbridge):
    feed_frames(websocket, {"event": "chat.send", "user_id": "forged", "agent_id": "a1"})

    await chat_handler(websocket, "joshu-a")

    session_id = commbridge.connect.await_args.args[0]
    routes.av_cache.presence_join.assert_awaited_once_with(
        websocket, "trusted-user", "a1", session_id, session_id=session_id
    )
    routes.av_cache.presence_leave.assert_awaited_once_with(
        websocket, "trusted-user", "a1", session_id, session_id=session_id
    )
    commbridge.disconnect.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_presence_is_released_after_handlers_are_cancelled(websocket, dispatch):
    order = []
    started = asyncio.Event()

    async def never_finishes(data):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append("handler_cancelled")

    async def leave(*args, **kwargs):
        order.append("presence_released")
        return 1

    dispatch.side_effect = never_finishes
    routes.av_cache.presence_leave.side_effect = leave
    disconnect_after_dispatch_starts(websocket, started, agent_id="a1")

    await chat_handler(websocket, "joshu-a")

    assert order == ["handler_cancelled", "presence_released"]
    routes.av_cache.presence_leave.assert_awaited_once()


@pytest.mark.asyncio
async def test_in_flight_handlers_are_cancelled(websocket, dispatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    handler_tasks = []

    async def never_finishes(data):
        handler_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    dispatch.side_effect = never_finishes
    disconnect_after_dispatch_starts(websocket, started)

    await chat_handler(websocket, "joshu-a")

    assert cancelled.is_set()
    assert len(handler_tasks) == 1
    assert handler_tasks[0].done()
    assert handler_tasks[0].cancelled()


@pytest.mark.asyncio
async def test_joshua_dispatches_sequentially(websocket, commbridge, dispatch):
    overlapping = []
    running = 0

    async def slow_dispatch(data):
        nonlocal running
        running += 1
        overlapping.append(running)
        await asyncio.sleep(0)
        running -= 1

    dispatch.side_effect = slow_dispatch
    feed_frames(
        websocket,
        {"event": "joshu-a.create", "data": {}},
        {"event": "joshu-a.comm", "data": {}},
    )

    await joshua_handler(websocket)

    assert overlapping == [1, 1]
    assert dispatch.await_count == 2
    commbridge.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_every_frame_is_dispatched_including_the_first(websocket, dispatch):
    feed_frames(
        websocket,
        {"event": "Joshu-A.Create", "data": {}},
        {"event": "joshu-a.comm", "data": {}},
    )

    await joshua_handler(websocket)

    assert dispatch.await_count == 2
    session_ids = {call.args[0]["_session_id"] for call in dispatch.await_args_list}
    responses = [call.args[0]["event"] for call in websocket.send_json.await_args_list]
    assert responses == ["joshu-a.create", "joshu-a.comm"]
    assert len(session_ids) == 1 and None not in session_ids


@pytest.mark.parametrize(
    ("first_message", "path_name", "expected"),
    [
        ({"event": "chat.send", "user_id": "forged", "agent_id": "a1"}, "eva", ("chat", "a1")),
        ({"event": "joshu-a.create"}, "joshu-a", ("joshu-a", None)),
        ({"event": "", "agent_system_name": "EVA"}, "fallback", ("eva", None)),
        ({}, "FromPath", ("frompath", None)),
    ],
)
@pytest.mark.asyncio
async def test_identity_is_negotiated_from_the_first_frame(
    first_message, path_name, expected, websocket, commbridge, dispatch, session_dependencies
):
    feed_frames(websocket, first_message)

    await chat_handler(websocket, path_name)

    ctx = session_dependencies[0]
    assert (ctx.agent_system_name, ctx.agent_id) == expected
    assert ctx.user_id == "trusted-user"
    assert ctx.tenant_id == "trusted-tenant"
    commbridge.subscribe.assert_awaited_once_with(
        channel=user_event_channel("trusted-user"), socket_id=ctx.session_id
    )
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args[0]["_session_id"] == ctx.session_id


@pytest.mark.parametrize("route", ["agent", "joshua"])
@pytest.mark.parametrize("failure_stage", ["subscribe", "register"])
@pytest.mark.asyncio
async def test_setup_failure_after_connect_releases_bridge(
    route, failure_stage, monkeypatch, websocket, commbridge
):
    error = RuntimeError(f"{failure_stage} failed")
    if failure_stage == "subscribe":
        commbridge.subscribe.side_effect = error
    else:
        monkeypatch.setattr(routes, "register_message_events", Mock(side_effect=error))
    feed_frames(websocket, {"event": "chat.send"})

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        if route == "agent":
            await chat_handler(websocket, "eva")
        else:
            await joshua_handler(websocket)

    commbridge.connect.assert_awaited_once()
    commbridge.disconnect.assert_awaited_once_with(commbridge.connect.await_args.args[0])
    assert websocket not in routes.connected_clients
    assert request_context.get() is None


@pytest.mark.asyncio
async def test_joshua_connect_failure_does_not_disconnect(websocket, commbridge):
    commbridge.connect.side_effect = RuntimeError("redis down")

    with pytest.raises(RuntimeError, match="redis down"):
        await joshua_handler(websocket)

    commbridge.subscribe.assert_not_awaited()
    commbridge.disconnect.assert_not_awaited()
    assert request_context.get() is None


@pytest.mark.parametrize("route", ["agent", "joshua"])
@pytest.mark.asyncio
async def test_missing_authenticated_identity_fails_closed_and_releases_local_state(
    route, websocket, commbridge
):
    websocket.scope.pop("user")

    try:
        with pytest.raises(RuntimeError, match="Authenticated WebSocket has no user_id"):
            if route == "agent":
                await chat_handler(websocket, "eva")
            else:
                await joshua_handler(websocket)

        commbridge.connect.assert_not_awaited()
        commbridge.subscribe.assert_not_awaited()
        commbridge.disconnect.assert_not_awaited()
        assert websocket not in routes.connected_clients
        assert request_context.get() is None
    finally:
        # Also isolate the intentionally failing, pre-fix regression run.
        routes.connected_clients.discard(websocket)


@pytest.mark.asyncio
async def test_disconnect_failure_cannot_skip_task_and_presence_cleanup(
    websocket, commbridge, dispatch
):
    before = asyncio.all_tasks()
    order = []
    started = asyncio.Event()

    async def running_handler(data):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append("handler_cancelled")

    async def leave(*args, **kwargs):
        order.append("presence_released")
        return 1

    async def disconnect(*args):
        order.append("disconnect")
        raise RuntimeError("disconnect failed")

    dispatch.side_effect = running_handler
    routes.av_cache.presence_leave.side_effect = leave
    commbridge.disconnect.side_effect = disconnect
    disconnect_after_dispatch_starts(websocket, started, agent_id="a1")

    try:
        with pytest.raises(RuntimeError, match="disconnect failed"):
            await chat_handler(websocket, "eva")

        assert order == ["handler_cancelled", "presence_released", "disconnect"]
        commbridge.disconnect.assert_awaited_once()
        assert websocket not in routes.connected_clients
        assert request_context.get() is None
        assert not (asyncio.all_tasks() - before)
    finally:
        # Do not leak tasks into subsequent tests when proving the old defect.
        leaked = asyncio.all_tasks() - before
        for task in leaked:
            task.cancel()
        await asyncio.gather(*leaked, return_exceptions=True)
        routes.connected_clients.discard(websocket)
        routes.clear_log_context()


@pytest.mark.asyncio
async def test_route_cancellation_drains_handlers_and_session_tasks(
    websocket, commbridge, dispatch
):
    before = asyncio.all_tasks()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def running_handler(data):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    dispatch.side_effect = running_handler
    # Keep receive blocked so cancellation, not a peer disconnect, ends the route.
    first = True

    async def blocking_receive():
        nonlocal first
        if first:
            first = False
            return {"event": "chat.send", "agent_id": "a1"}
        await asyncio.Event().wait()

    websocket.receive_json = AsyncMock(side_effect=blocking_receive)
    route_task = asyncio.create_task(chat_handler(websocket, "eva"))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        route_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await route_task

        assert cancelled.is_set()
        routes.av_cache.presence_leave.assert_awaited_once()
        commbridge.disconnect.assert_awaited_once()
        assert websocket not in routes.connected_clients
        assert not (asyncio.all_tasks() - before)
    finally:
        leaked = asyncio.all_tasks() - before
        for task in leaked:
            task.cancel()
        await asyncio.gather(*leaked, return_exceptions=True)
        routes.connected_clients.discard(websocket)


@pytest.mark.asyncio
async def test_last_socket_sleeps_and_evicts_only_its_runtime(monkeypatch, websocket, commbridge):
    registry = websocket.app.container.agentverse.instance_registry
    agent = SimpleNamespace(sleep=AsyncMock())
    other_agent = SimpleNamespace(sleep=AsyncMock())
    registry.set("trusted-user", "a1", other_agent, "other-session")

    async def connect(session_id, socket, lock):
        registry.set("trusted-user", "a1", agent, session_id)

    @asynccontextmanager
    async def spawn_lock(*args, **kwargs):
        yield

    commbridge.connect.side_effect = connect
    routes.av_cache.presence_leave.return_value = 0
    repo = SimpleNamespace(scard=AsyncMock(return_value=0))
    monkeypatch.setattr(routes.av_cache, "acquire_spawn_lock", spawn_lock)
    monkeypatch.setattr(routes.av_cache, "get_cache_repo", Mock(return_value=repo))
    monkeypatch.setattr(routes.av_cache, "presence_sockets_key", Mock(return_value="presence-key"))
    feed_frames(websocket, {"event": "chat.send", "agent_id": "a1"})

    await chat_handler(websocket, "eva")

    session_id = commbridge.connect.await_args.args[0]
    agent.sleep.assert_awaited_once_with(websocket)
    assert not registry.exists("trusted-user", "a1", session_id)
    assert registry.get("trusted-user", "a1", "other-session") is other_agent
    other_agent.sleep.assert_not_awaited()
    repo.scard.assert_awaited_once_with("presence-key")
    commbridge.disconnect.assert_awaited_once_with(session_id)
