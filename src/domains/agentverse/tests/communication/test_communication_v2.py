"""Real Redis, UTU, lifecycle, CPBX and ASGI WebSocket slice. No external LLM."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, WebSocketDisconnect

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import AgentActivationApproval
from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.agents.lifecycle.tests.test_session_authority import (
    session_env as session_env,  # noqa: PLC0414 - pytest fixture re-export
)
from src.domains.agentverse.agents.lifecycle.tests.test_session_authority import (
    store as store,  # noqa: PLC0414
)
from src.domains.agentverse.agents.runtime_v2 import ManagedAgentV2
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.interfaces.api.v2.communication import (
    BASE,
    communication_socket,
    router,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.repositories.conversation_v2 import ConversationRepositoryV2
from src.domains.agentverse.services.client_event_dispatcher_v2 import ClientEventDispatcherV2
from src.domains.agentverse.tools.chat_tool_v2.synaptic import chat_context


@pytest_asyncio.fixture
async def communication(session_env):  # noqa: F811 - fixture re-export
    s = session_env
    e = s.env
    e.policy = SynapticAuthorization(
        grants={("tenant", a): SynapticGrants(["chat.context"]) for a in ("agent", "second")}
    )
    await e.utu.install_policy(
        "tenant", expected=await s.store.current_policy("tenant"), authorization=e.policy
    )
    e.verifier._approvals[("tenant", "second")] = AgentActivationApproval(
        "tenant",
        "second",
        "owner",
        True,
    )
    e.definitions.find_by_id.side_effect = lambda agent: {
        "id": agent,
        "creator": "owner",
        "runtime_version": "v2",
        "name": "Adam",
    }
    registry = SynapticRegistry()
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()
    block = [False]
    active = [0]
    peak = [0]

    @registry.subscribe("chat.context", provider="tool.chat")
    async def handler(synapse, client):
        calls.append(synapse)
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            if block[0]:
                entered.set()
                await release.wait()
            return await chat_context(synapse, client)
        finally:
            active[0] -= 1

    exchange = SynapticExchange(registry=registry, authorization=e.utu)

    async def build(key):
        async def connect(k):
            return exchange.bind(await e.utu.resolve_agent_principal(k))

        return ManagedAgentV2(key=key, ncsc_connector=connect)

    e.factory.build.side_effect = build
    prefix = "test:communication:" + uuid4().hex
    repository = ConversationRepositoryV2(s.store._repo, prefix=prefix)
    dispatcher = ClientEventDispatcherV2(
        conversations=repository,
        authority=e.utu,
        khepri=Khepri(lifecycle=e.lifecycle),
        instances=e.instances,
    )
    conversation = await repository.create(
        ConversationV2(
            tenant_id="tenant",
            member_user_ids=("owner", "collaborator"),
            agent_ids=("agent", "second"),
        )
    )
    event = ClientEventV2(
        conversation_id=conversation.id,
        event_id="turn-1",
        type="chat.message",
        agent_ids=("agent",),
        message="Hello Adam",
    )
    app = FastAPI()
    app.include_router(router)
    app.state.genesis_sessions = s.sessions
    app.container = SimpleNamespace(
        agentverse=SimpleNamespace(
            conversation_repository_v2=lambda: repository,
            client_event_dispatcher_v2=lambda: dispatcher,
            utu_authority=lambda: e.utu,
        )
    )
    try:
        yield SimpleNamespace(**locals())
    finally:
        await e.lifecycle.shutdown()
        keys = [k async for k in s.store._repo.redis.scan_iter(prefix + "*")]
        if keys:
            await s.store._repo.redis.delete(*keys)


async def issue(c):
    async with c.s.sessions.authenticated(c.s.token):
        return await c.s.sessions.issue_ws_ticket()


class Socket:
    """ASGI-compatible route harness with independent receive/send queues."""

    def __init__(self, app):
        self.app = app
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.closed = asyncio.Event()
        self.scope = {}
        self.close_code = None
        self.accept = AsyncMock()

    async def receive_text(self):
        value = await self.input.get()
        if value is None:
            raise WebSocketDisconnect()
        return json.dumps(value)

    async def send_json(self, value):
        if self.closed.is_set():
            raise WebSocketDisconnect()
        await self.output.put(value)

    async def close(self, code):
        self.close_code = code
        self.closed.set()

    async def read(self):
        return await asyncio.wait_for(self.output.get(), 2)


async def connect(c):
    socket = Socket(c.app)
    await socket.input.put({"type": "authenticate", "ticket": await issue(c)})
    task = asyncio.create_task(communication_socket(socket))
    assert (await socket.read())["type"] == "session.ready"
    return socket, task


@pytest.mark.asyncio
async def test_http_auth_creation_and_persistence(communication):
    c = communication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(c.app), base_url="http://test"
    ) as client:
        assert (await client.post(BASE + "/ticket")).status_code == 403
        headers = {"authorization": "Bearer " + c.s.token}
        ticket = await client.post(BASE + "/ticket", headers=headers)
        assert ticket.status_code == 201 and ticket.headers["cache-control"] == "no-store"
        result = await client.post(
            BASE + "/conversations", headers=headers, json={"agent_ids": ["agent"]}
        )
        assert result.status_code == 201
        document = result.json()
        assert document["member_user_ids"] == ["owner"]
        fresh = ConversationRepositoryV2(c.s.store._repo, prefix=c.prefix)
        assert (await fresh.get("tenant", document["id"])).id == document["id"]
        for body in ({"agent_ids": ["second"]}, {"agent_ids": ["agent"], "tenant_id": "evil"}):
            assert (
                await client.post(BASE + "/conversations", headers=headers, json=body)
            ).status_code in (403, 422)
        assert (
            await client.get(BASE + "/conversations/unknown", headers=headers)
        ).status_code == 403


@pytest.mark.asyncio
async def test_ticket_single_use_bad_handshake_expiry_and_logout(communication):
    c = communication
    token = await issue(c)
    async with c.s.sessions.authenticated_ticket(token):
        assert (await c.s.sessions.get_session("session")).user_id == "owner"
    with pytest.raises(EventPermissionError):
        async with c.s.sessions.authenticated_ticket(token):
            pass
    for handshake in ({"type": "authenticate", "ticket": "invalid"}, {"type": "ping"}, []):
        socket = Socket(c.app)
        await socket.input.put(handshake)
        await communication_socket(socket)
        assert socket.close_code == 1008
    token = await issue(c)
    c.s.now[0] = c.s.claims["exp"] + 1
    with pytest.raises(EventPermissionError):
        async with c.s.sessions.authenticated_ticket(token):
            pass


@pytest.mark.asyncio
async def test_ws_context_correlation_replay_conflict_and_disconnect(communication):
    c = communication
    socket, task = await connect(c)
    try:
        await socket.input.put(c.event.model_dump())
        response = await socket.read()
        assert response["type"] == "chat.context"
        assert response["correlation_id"] == c.event.event_id
        assert response["conversation_id"] == c.conversation.id
        assert response["results"][0]["agent_id"] == "agent"
        assert response["results"][0]["context"]["usage_meta"]["provider"] == "chat_v2"
        assert c.calls[0].payload["text"] == "Hello Adam"
        assert c.calls[0].source == "agent" and c.calls[0].correlation_id == "turn-1"
        await socket.input.put(c.event.model_dump())
        assert await socket.read() == response
        assert len(c.calls) == 1
        await socket.input.put(c.event.model_copy(update={"message": "changed"}).model_dump())
        assert (await socket.read())["code"] == "conflict"
    finally:
        await socket.input.put(None)
        await asyncio.wait_for(task, 2)
    # Socket lifetime is independent of conversation/runtime lifetime.
    assert (await c.repository.get("tenant", c.conversation.id)).id == c.conversation.id
    again, again_task = await connect(c)
    await again.input.put(None)
    await again_task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["tenant", "membership", "target", "grant", "unapproved", "disabled"]
)
async def test_denial_before_lifecycle_or_cpbx(communication, mode):
    c = communication
    conversation = c.conversation
    event = c.event
    if mode == "tenant":
        conversation = conversation.model_copy(update={"id": uuid4().hex, "tenant_id": "other"})
    elif mode == "membership":
        conversation = conversation.model_copy(
            update={"id": uuid4().hex, "member_user_ids": ("other",)}
        )
    elif mode == "target":
        event = event.model_copy(update={"agent_ids": ("second",)})
    elif mode == "grant":
        policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants(["other"])})
        await c.e.utu.install_policy(
            "tenant", expected=await c.s.store.current_policy("tenant"), authorization=policy
        )
    elif mode == "unapproved":
        c.e.verifier._approvals.pop(("tenant", "agent"))
    elif mode == "disabled":
        c.e.definitions.find_by_id.side_effect = lambda agent: {
            "id": agent,
            "creator": "owner",
            "runtime_version": "v2",
            "enabled": False,
        }
    if mode in {"tenant", "membership"}:
        await c.repository.create(conversation)
        event = event.model_copy(update={"conversation_id": conversation.id})
    async with c.s.sessions.authenticated(c.s.token) as session:
        with pytest.raises(EventPermissionError):
            await c.dispatcher.dispatch(session, event)
    c.e.factory.build.assert_not_awaited()
    assert not c.calls


@pytest.mark.asyncio
async def test_multi_agent_contract_keeps_agent_principals_separate(communication):
    c = communication
    token = c.s.sign(sid="multi")
    await c.s.sessions.provision(
        token, tenant_id="tenant", user_id="owner", allowed_agent_ids=frozenset({"agent", "second"})
    )
    event = c.event.model_copy(update={"agent_ids": ("agent", "second")})
    async with c.s.sessions.authenticated(token) as session:
        result = await c.dispatcher.dispatch(session, event)
    assert [x["agent_id"] for x in result["results"]] == ["agent", "second"]
    assert [s.source for s in c.calls] == ["agent", "second"]
    assert all(s.source != "owner" for s in c.calls)
    # Another member can see the conversation, but cannot execute its owner's agents.
    other = c.s.sign(sid="collaborator", sub="collaborator")
    await c.s.sessions.provision(
        other, tenant_id="tenant", user_id="collaborator", allowed_agent_ids=frozenset({"agent"})
    )
    async with c.s.sessions.authenticated(other) as session:
        await c.e.utu.authorize_conversation(
            session_id=session.session_id, conversation=c.conversation
        )
        with pytest.raises(EventPermissionError):
            await c.dispatcher.dispatch(session, event)


@pytest.mark.asyncio
async def test_concurrent_turns_bounded_backpressure_and_one_socket(communication):
    c = communication
    c.block[0] = True
    socket, task = await connect(c)
    second = Socket(c.app)
    await second.input.put({"type": "authenticate", "ticket": await issue(c)})
    await communication_socket(second)
    assert second.close_code == 1008
    try:
        await socket.input.put(c.event.model_dump())
        await asyncio.wait_for(c.entered.wait(), 2)
        # A second worker cannot overlap an admitted turn in the same conversation.
        async with c.s.sessions.authenticated(c.s.token) as session:
            busy = await c.dispatcher.dispatch(
                session, c.event.model_copy(update={"event_id": "remote"})
            )
        assert busy["code"] == "busy"
        for i in range(1, 9):
            await socket.input.put(
                c.event.model_copy(update={"event_id": f"turn-{i + 1}"}).model_dump()
            )
        assert (await socket.read())["code"] == "backpressure"
        c.release.set()
        results = [await socket.read() for _ in range(8)]
        assert all(r["type"] == "chat.context" for r in results)
        assert c.peak[0] == 1
    finally:
        await socket.input.put(None)
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["disconnect", "cancel", "revoke", "remote-revoke"])
async def test_inflight_cancellation_and_revocation(communication, stop):
    c = communication
    c.block[0] = True
    socket, task = await connect(c)
    await socket.input.put(c.event.model_dump())
    await asyncio.wait_for(c.entered.wait(), 2)
    if stop == "disconnect":
        await socket.input.put(None)
    elif stop == "cancel":
        await socket.input.put(
            {
                "type": "event.cancel",
                "conversation_id": c.conversation.id,
                "event_id": "cancel-1",
                "cancel_event_id": c.event.event_id,
            }
        )
        assert (await socket.read())["code"] == "cancelled"
        assert (await socket.read())["type"] == "event.cancelled"
        await socket.input.put(None)
    elif stop == "revoke":
        await c.s.sessions.invalidate("session")
    else:
        await c.s.store.revoke("tenant", session_id="session")
        # Remote UTU revocation cancels agent work even while the session is still active.
        assert (await socket.read())["code"] == "cancelled"
        await socket.input.put(None)
    await asyncio.wait_for(task, 3)
    assert c.active[0] == 0
    status, response = await c.repository.begin("tenant", "owner", c.event)
    assert status == "replay" and response["code"] == "cancelled"


def test_envelope_rejects_identity_grants_unbounded_targets_and_duplicate_targets():
    base = {
        "conversation_id": "c",
        "event_id": "e",
        "type": "chat.message",
        "agent_ids": ["a"],
        "message": "hi",
    }
    for change in (
        {"user_id": "other"},
        {"tenant_id": "other"},
        {"signal": "llm.generate"},
        {"agent_ids": ["a"] * 2},
        {"agent_ids": list("abcde")},
    ):
        with pytest.raises(ValueError):
            ClientEventV2.model_validate({**base, **change})


@pytest.mark.asyncio
async def test_asgi_route_to_adam_context(communication):
    c = communication
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/ws/api/v2/caos",
        "raw_path": b"/ws/api/v2/caos",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "subprotocols": [],
        "root_path": "",
    }
    task = asyncio.create_task(c.app(scope, incoming.get, outgoing.put))

    async def read():
        return await asyncio.wait_for(outgoing.get(), 2)

    try:
        assert (await read())["type"] == "websocket.accept"
        await incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "authenticate",
                        "ticket": await issue(c),
                    }
                ),
            }
        )
        assert json.loads((await read())["text"])["type"] == "session.ready"
        await incoming.put({"type": "websocket.receive", "text": c.event.model_dump_json()})
        result = json.loads((await read())["text"])
        assert result["type"] == "chat.context" and result["correlation_id"] == "turn-1"
        assert result["results"][0]["context"]["usage_meta"]["provider"] == "chat_v2"
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_journal_fails_closed_pending_and_capacity(communication):
    c = communication
    assert (await c.repository.begin("tenant", "owner", c.event))[0] == "started"
    fresh = ConversationRepositoryV2(c.s.store._repo, prefix=c.prefix)
    assert (await fresh.begin("tenant", "owner", c.event))[0] == "pending"
    response = {"type": "event.error", "code": "cancelled", "arrays": []}
    await fresh.finish("tenant", "owner", c.event, response)
    assert (await fresh.begin("tenant", "owner", c.event)) == ("replay", response)
    for i in range(255):
        event = c.event.model_copy(update={"event_id": f"capacity-{i}"})
        assert (await fresh.begin("tenant", "owner", event))[0] == "started"
        await fresh.finish("tenant", "owner", event, response)
    assert (await fresh.begin("tenant", "owner", c.event.model_copy(update={"event_id": "full"})))[
        0
    ] == "full"


@pytest.mark.asyncio
async def test_completed_replay_requires_current_authority(communication):
    c = communication
    async with c.s.sessions.authenticated(c.s.token) as session:
        assert (await c.dispatcher.dispatch(session, c.event))["type"] == "chat.context"
        await c.e.utu.revoke("tenant", agent_id="agent")
        with pytest.raises(EventPermissionError):
            await c.dispatcher.dispatch(session, c.event)
    assert len(c.calls) == 1


@pytest.mark.asyncio
async def test_ticket_ttl_consumption_and_revocation_before_redemption(communication):
    c = communication
    ticket = await issue(c)
    key = c.s.sessions._keys[0] + ":ticket:" + c.s.sessions._field(ticket)
    assert 0 < await c.s.store._repo.redis.ttl(key) <= 30
    await c.s.store._repo.redis.expire(key, 0)
    with pytest.raises(EventPermissionError):
        async with c.s.sessions.authenticated_ticket(ticket):
            pass
    ticket = await issue(c)
    await c.s.sessions.invalidate("session")
    with pytest.raises(EventPermissionError):
        async with c.s.sessions.authenticated_ticket(ticket):
            pass
