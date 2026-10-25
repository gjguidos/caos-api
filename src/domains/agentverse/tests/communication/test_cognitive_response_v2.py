"""Governed response, real Redis transcript/fences and real UTU/CPBX; fake model only."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.personalities import empty_eva  # noqa: F401 - register default
from src.domains.agentverse.registries import role_registry_instance
from src.domains.agentverse.services.cognitive_response_v2 import CognitiveResponseV2
from src.domains.agentverse.tests.communication import test_communication_v2 as fixtures

communication = fixtures.communication
session_env = fixtures.session_env
store = fixtures.store


@pytest_asyncio.fixture
async def response_env(communication):
    c = communication
    role_registry_instance.register_role_spec(role_spec)
    definition = {
        "id": "agent",
        "creator": "owner",
        "tenant_id": "tenant",
        "enabled": True,
        "runtime_version": "v2",
        "type": "chat",
        "name": "Adam",
        "prompt": "Remember the user's chosen constellation.",
        "llm_type": "registered-model",
    }
    c.e.definitions.find_by_id.side_effect = lambda _: definition.copy()
    repo = SimpleNamespace(
        get_response_async=AsyncMock(return_value="I remember Orion."),
        client=SimpleNamespace(provider="test-provider", chat_deployment="test-model"),
    )
    factory = ClientFactory({"llm": {"registered-model": repo}}, {"llm": "registered-model"})
    policy = SynapticAuthorization(
        grants={("tenant", "agent"): SynapticGrants(["chat.context", "chat.response"])}
    )
    await c.e.utu.install_policy(
        "tenant", expected=await c.s.store.current_policy("tenant"), authorization=policy
    )
    service = CognitiveResponseV2(
        definitions=c.e.definitions,
        authority=c.e.utu,
        history=c.dispatcher.history,
        client_factory=lambda: factory,
    )
    c.registry.subscribe("chat.response", provider="cognitive.chat")(service.handle)
    c.event = c.event.model_copy(
        update={"response_mode": "llm", "message": "My constellation is Orion."}
    )
    return SimpleNamespace(c=c, repo=repo, factory=factory, definition=definition, service=service)


@pytest.mark.asyncio
async def test_response_attribution_history_and_idempotent_retry(response_env, monkeypatch):
    r = response_env
    c = r.c
    # Any accidental V1 construction makes this integration test fail.
    from src.domains.agentverse.agents.factory import AgentFactory

    monkeypatch.setattr(
        AgentFactory, "build_agent", AsyncMock(side_effect=AssertionError("V1 fallback"))
    )
    async with c.s.sessions.authenticated(c.s.token) as session:
        result = await c.dispatcher.dispatch(session, c.event)
        assert result["type"] == "chat.response", result
        assert result["correlation_id"] == c.event.event_id
        assert result["results"][0]["response"]["kind"] == "agent_response"
        assert result["results"][0]["response"]["agent_id"] == "agent"
        assert result["results"][0]["llm"] == {
            "key": "registered-model",
            "provider": "test-provider",
            "model": "test-model",
        }
        assert await c.dispatcher.dispatch(session, c.event) == result
        assert r.repo.get_response_async.await_count == 1
        rows = await c.repository.read_messages("tenant", c.conversation.id)
        assert [m.kind for m in rows] == ["user_message", "agent_response"]
        assert rows[1].content == "I remember Orion."
        assert rows[1].capability is None
        # Context remains deterministic and never becomes an assistant history entry.
        context_event = c.event.model_copy(
            update={"event_id": "context-turn", "response_mode": "context"}
        )
        assert (await c.dispatcher.dispatch(session, context_event))["type"] == "chat.context"
        followup = c.event.model_copy(
            update={"event_id": "followup", "message": "Which constellation?"}
        )
        assert (await c.dispatcher.dispatch(session, followup))["type"] == "chat.response"
        messages = r.repo.get_response_async.call_args.kwargs["input"]
        assert {"role": "assistant", "content": "I remember Orion."} in messages
        assert messages[-1] == {"role": "user", "content": "Which constellation?"}
        assert not any("usage_meta" in m["content"] for m in messages)
        assert "Adam" in r.repo.get_response_async.call_args.kwargs["instructions"]
        assert (
            await c.dispatcher.dispatch(session, followup.model_copy(update={"message": "changed"}))
        )["code"] == "conflict"
    assert AgentFactory.build_agent.await_count == 0


@pytest.mark.asyncio
async def test_missing_llm_grant_denies_before_provider_and_replay(response_env):
    r = response_env
    c = r.c
    async with c.s.sessions.authenticated(c.s.token) as session:
        assert (await c.dispatcher.dispatch(session, c.event))["type"] == "chat.response"
        policy = SynapticAuthorization(
            grants={("tenant", "agent"): SynapticGrants(["chat.context"])}
        )
        await c.e.utu.install_policy(
            "tenant", expected=await c.s.store.current_policy("tenant"), authorization=policy
        )
        with pytest.raises(EventPermissionError):
            await c.dispatcher.dispatch(session, c.event)
        with pytest.raises(EventPermissionError):
            await c.dispatcher.dispatch(session, c.event.model_copy(update={"event_id": "new"}))
        assert r.repo.get_response_async.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["provider", "empty", "error", "v1", "type", "role", "personality", "pack"]
)
async def test_fail_closed_without_transcript_or_v1_fallback(response_env, failure):
    r = response_env
    c = r.c
    code = "composition_unavailable"
    if failure == "provider":
        r.factory.repos["llm"].clear()
        code = "llm_provider_unavailable"
    elif failure == "empty":
        r.repo.get_response_async.return_value = ""
        code = "llm_response_invalid"
    elif failure == "error":
        r.repo.get_response_async.side_effect = RuntimeError("sensitive provider detail")
        code = "llm_provider_failed"
    elif failure == "v1":
        r.definition["runtime_version"] = "v1"
        code = "denied"
    else:
        r.definition[
            {
                "type": "type",
                "role": "role_name",
                "personality": "personality_key",
                "pack": "rule_pack_ids",
            }[failure]
        ] = ["missing"] if failure == "pack" else "missing"
    async with c.s.sessions.authenticated(c.s.token) as session:
        result = await c.dispatcher.dispatch(session, c.event)
        assert result["code"] == code, result
        assert await c.repository.read_messages("tenant", c.conversation.id) == ()
        assert "sensitive" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "revoke", "remote-revoke", "policy"])
async def test_inflight_llm_cancellation_revocation_and_atomic_no_append(response_env, stop):
    r = response_env
    c = r.c
    entered = asyncio.Event()

    async def model(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    r.repo.get_response_async.side_effect = model
    async with c.s.sessions.authenticated(c.s.token) as session:
        task = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(entered.wait(), 2)
        if stop == "cancel":
            task.cancel()
        elif stop == "revoke":
            await c.e.utu.revoke("tenant", agent_id="agent")
        elif stop == "remote-revoke":
            await c.s.store.revoke("tenant", session_id="session")
        else:
            await c.e.utu.install_policy(
                "tenant",
                expected=await c.s.store.current_policy("tenant"),
                authorization=SynapticAuthorization(),
            )
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert await c.repository.read_messages("tenant", c.conversation.id) == ()
        status, result = await c.repository.begin("tenant", "owner", c.event)
        assert status == "replay" and result["code"] == "cancelled"


@pytest.mark.asyncio
async def test_actual_websocket_response_and_session_denial(response_env):
    r = response_env
    c = r.c
    socket, task = await fixtures.connect(c)
    try:
        await socket.input.put(c.event.model_dump())
        result = await socket.read()
        assert result["type"] == "chat.response", result
        assert result["correlation_id"] == c.event.event_id
        await socket.input.put(c.event.model_dump())
        assert await socket.read() == result
        await c.s.sessions.invalidate("session")
        await asyncio.wait_for(task, 3)
        assert socket.close_code == 1008
    finally:
        if not task.done():
            await socket.input.put(None)
            await task


@pytest.mark.asyncio
async def test_response_other_tenant_and_member_denied_without_inference(response_env):
    r = response_env
    c = r.c
    from uuid import uuid4

    async with c.s.sessions.authenticated(c.s.token) as session:
        for change in ({"tenant_id": "other"}, {"member_user_ids": ("other",)}):
            doc = c.conversation.model_copy(update={"id": uuid4().hex, **change})
            await c.repository.create(doc)
            with pytest.raises(EventPermissionError):
                await c.dispatcher.dispatch(
                    session, c.event.model_copy(update={"conversation_id": doc.id})
                )
    r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_trusted_seed_uses_registered_runtime_default(response_env):
    r = response_env
    c = r.c
    r.definition.update(genesis_local_seed=True, llm_type="openai")
    async with c.s.sessions.authenticated(c.s.token) as session:
        result = await c.dispatcher.dispatch(session, c.event)
    assert result["results"][0]["llm"]["key"] == "registered-model"


@pytest.mark.asyncio
async def test_cancelled_model_cannot_suppress_cancellation_and_commit(response_env):
    r = response_env
    c = r.c
    entered = asyncio.Event()

    async def suppress(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "Must never be committed"

    r.repo.get_response_async.side_effect = suppress
    async with c.s.sessions.authenticated(c.s.token) as session:
        task = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await c.repository.read_messages("tenant", c.conversation.id) == ()


@pytest.mark.asyncio
async def test_seed_invalid_explicit_model_does_not_fall_back(response_env):
    r = response_env
    r.definition.update(genesis_local_seed=True, llm_type="unavailable-explicit-model")
    async with r.c.s.sessions.authenticated(r.c.s.token) as session:
        result = await r.c.dispatcher.dispatch(session, r.c.event)
    assert result["code"] == "llm_provider_unavailable"
    r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_context_journal_digest_stays_replayable(response_env):
    import hashlib
    import json

    c = response_env.c
    event = c.event.model_copy(update={"event_id": "old-format", "response_mode": "context"})
    digest = hashlib.sha256(event.model_dump_json(exclude={"response_mode"}).encode()).hexdigest()
    response = {"type": "chat.context", "results": []}
    await c.repository.repository.eval(
        "return redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])",
        keys=c.repository._keys("tenant", c.conversation.id)[1:2],
        args=[
            json.dumps(["owner", event.event_id]),
            json.dumps({"digest": digest, "response": json.dumps(response)}),
        ],
    )
    assert await c.repository.begin("tenant", "owner", event) == ("replay", response)
