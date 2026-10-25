"""Real Redis transcript, trusted UTU and deterministic CPBX integration."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.cognitive_v2 import (
    AgentCognitiveProfile,
    CognitiveContext,
    CognitiveResult,
    CognitiveTurn,
)
from src.domains.agentverse.entities.history_v2 import AgentHistoryViewV2, ConversationMessageV2
from src.domains.agentverse.interfaces.api.v2.communication import BASE
from src.domains.agentverse.repositories.conversation_v2 import ConversationRepositoryV2
from src.domains.agentverse.tests.communication import test_communication_v2 as fixtures

# Re-export integration fixtures without changing their security setup.
communication = fixtures.communication
session_env = fixtures.session_env
store = fixtures.store


async def append(c, index, **overrides):
    event = c.event.model_copy(update={"event_id": f"fixture-{index}"})
    message = ConversationMessageV2(
        **(
            {
                "id": f"message-{index}",
                "tenant_id": "tenant",
                "conversation_id": c.conversation.id,
                "event_id": event.event_id,
                "turn_user_id": "owner",
                "participant_id": "owner",
                "kind": "user_message",
                "content": f"text-{index}",
                "visible_agent_ids": ("agent",),
            }
            | overrides
        )
    )
    assert (await c.repository.begin("tenant", "owner", event))[0] == "started"
    await c.repository.finish(
        "tenant",
        "owner",
        event,
        {"type": "fixture"},
        messages=(message,),
        expected_document=c.conversation,
    )
    return message


@pytest.mark.asyncio
async def test_transcript_durable_replay_order_and_prior_context(communication):
    c = communication
    async with c.s.sessions.authenticated(c.s.token) as session:
        first = await c.dispatcher.dispatch(session, c.event)
        assert await c.dispatcher.dispatch(session, c.event) == first
        next_event = c.event.model_copy(update={"event_id": "turn-2", "message": "next"})
        assert (await c.dispatcher.dispatch(session, next_event))["type"] == "chat.context"
        page = await c.dispatcher.history.page(session, c.conversation.id)
    assert [m.sequence for m in page.messages] == [1, 2, 3, 4]
    assert [m.kind for m in page.messages] == ["user_message", "capability_contribution"] * 2
    assert c.calls[0].payload["history"] == []
    assert c.calls[1].payload["history"] == [{"role": "user", "content": "Hello Adam"}]
    fresh = ConversationRepositoryV2(c.s.store._repo, prefix=c.prefix)
    assert await fresh.read_messages("tenant", c.conversation.id) == page.messages
    assert await c.s.store._repo.redis.ttl(c.repository._keys("tenant", c.conversation.id)[3]) == -1


@pytest.mark.asyncio
async def test_history_http_auth_cursor_scope_visibility_and_current_membership(communication):
    c = communication
    await append(c, 0)
    await append(c, 1, visible_user_ids=("collaborator",))
    await append(c, 2, participant_id="second", kind="agent_response", content="Actual reply")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(c.app), base_url="http://test"
    ) as client:
        path = BASE + f"/conversations/{c.conversation.id}/history"
        assert (await client.get(path)).status_code == 403
        headers = {"authorization": "Bearer " + c.s.token}
        first = await client.get(path, headers=headers, params={"limit": 1})
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        assert first.json()["messages"][0]["id"] == "message-0"
        cursor = first.json()["next_cursor"]
        middle = await client.get(path, headers=headers, params={"limit": 1, "cursor": cursor})
        assert middle.json()["messages"] == []
        last = await client.get(
            path, headers=headers, params={"cursor": middle.json()["next_cursor"]}
        )
        assert [m["content"] for m in last.json()["messages"]] == ["Actual reply"]
        assert (
            await client.get(path, headers=headers, params={"cursor": "bad"})
        ).status_code == 422
        assert (await client.get(path, headers=headers, params={"limit": 101})).status_code == 422
        other = c.conversation.model_copy(update={"id": "other-conversation"})
        await c.repository.create(other)
        assert (
            await client.get(
                BASE + "/conversations/other-conversation/history",
                headers=headers,
                params={"cursor": cursor},
            )
        ).status_code == 422
        raw_key = c.repository._keys("tenant", c.conversation.id)[0]
        await c.s.store._repo.redis.set(
            raw_key,
            c.conversation.model_copy(
                update={"member_user_ids": ("collaborator",)}
            ).model_dump_json(),
        )
        assert (await client.get(path, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_projection_bounded_mixed_authors_and_visibility(communication):
    c = communication
    for i in range(30):
        await append(
            c,
            i,
            participant_id="second" if i % 2 else "owner",
            kind="agent_response" if i % 2 else "user_message",
        )
    await append(c, 30, visible_agent_ids=("second",))
    await append(c, 31, visible_user_ids=("collaborator",))
    await append(
        c,
        32,
        kind="capability_contribution",
        participant_id="agent",
        capability="chat.context",
        content='{"messages": []}',
    )
    async with c.s.sessions.authenticated(c.s.token) as session:
        view = await c.dispatcher.history.project(
            session, c.conversation.id, "agent", exclude_event_id="current"
        )
        assert len(view.messages) == 24
        assert view.messages[0].id == "message-6"
        assert view.chat_messages()[-1] == {
            "role": "assistant",
            "content": "[agent:second] text-29",
        }
        assert not any(m.id in {"message-30", "message-31", "message-32"} for m in view.messages)
        with pytest.raises(EventPermissionError):
            await c.dispatcher.history.page(replace(session, tenant_id="evil"), c.conversation.id)
        await c.e.utu.revoke("tenant", agent_id="agent")
        with pytest.raises(EventPermissionError):
            await c.dispatcher.history.project(
                session, c.conversation.id, "agent", exclude_event_id="current"
            )


@pytest.mark.asyncio
async def test_char_budget_and_current_event_exclusion(communication):
    c = communication
    for i in range(6):
        await append(c, i, content="x" * 8192)
    async with c.s.sessions.authenticated(c.s.token) as session:
        view = await c.dispatcher.history.project(
            session, c.conversation.id, "agent", exclude_event_id="fixture-5"
        )
        assert len(view.messages) == 4
        assert sum(len(m.content) for m in view.messages) == 32768
        assert view.messages[-1].event_id == "fixture-4"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "revocation", "membership"])
async def test_no_transcript_artifacts_on_cancel_or_revocation(communication, stop):
    c = communication
    c.block[0] = True
    async with c.s.sessions.authenticated(c.s.token) as session:
        task = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(c.entered.wait(), 2)
        if stop == "cancel":
            task.cancel()
        elif stop == "revocation":
            await c.e.utu.revoke("tenant", agent_id="agent")
        else:
            await c.s.store._repo.redis.set(
                c.repository._keys("tenant", c.conversation.id)[0],
                c.conversation.model_copy(update={"member_user_ids": ("other",)}).model_dump_json(),
            )
        c.release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert await c.repository.read_messages("tenant", c.conversation.id) == ()
    assert (await c.repository.begin("tenant", "owner", c.event))[0] == "replay"


@pytest.mark.asyncio
async def test_atomic_append_membership_cas_and_turn_owner(communication):
    c = communication
    assert (await c.repository.begin("tenant", "owner", c.event))[0] == "started"
    message = ConversationMessageV2(
        id="m",
        tenant_id="tenant",
        conversation_id=c.conversation.id,
        event_id=c.event.event_id,
        turn_user_id="owner",
        participant_id="owner",
        kind="user_message",
        content="hi",
    )
    with pytest.raises(ValueError):
        await c.repository.finish(
            "tenant", "other", c.event, {}, messages=(message,), expected_document=c.conversation
        )
    changed = c.conversation.model_copy(update={"agent_ids": ("second",)})
    with pytest.raises(EventPermissionError):
        await c.repository.finish(
            "tenant", "owner", c.event, {}, messages=(message,), expected_document=changed
        )
    assert await c.repository.read_messages("tenant", c.conversation.id) == ()
    await c.repository.finish("tenant", "owner", c.event, {"type": "event.error"})


def test_cognitive_contracts_immutable_and_keep_authority_separate():
    profile = AgentCognitiveProfile(
        agent_id="a",
        definition_version="0.2.1",
        type="chat",
        role="assistant",
        personality="calm",
        rule_packs=("rules@1",),
        capabilities=("chat.context",),
        cognitive_resources=("context@1",),
    )
    with pytest.raises(ValidationError):
        profile.role = "admin"
    with pytest.raises(ValidationError):
        AgentCognitiveProfile(
            **(profile.model_dump() | {"authorized_capabilities": ["llm.generate"]})
        )
    with pytest.raises(ValidationError):
        CognitiveResult(
            turn_id="t",
            agent_id="a",
            kind="agent_response",
            content="hints",
            capability="chat.context",
        )
    result = CognitiveResult(
        turn_id="t",
        agent_id="a",
        kind="capability_contribution",
        content="hints",
        capability="chat.context",
    )
    assert result.kind != "agent_response"
    turn = CognitiveTurn(
        id="t",
        tenant_id="tenant",
        conversation_id="c",
        user_id="u",
        agent_id="a",
        message="hi",
        correlation_id="t",
        causation_id="t",
    )
    with pytest.raises(TypeError, match="UTU principal"):
        CognitiveContext(profile, turn, AgentHistoryViewV2(agent_id="a"), {"agent_id": "a"}, ())


@pytest.mark.asyncio
async def test_trusted_cognitive_context_validates_principal_and_capability_snapshot(communication):
    c = communication
    from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

    async with c.s.sessions.authenticated(c.s.token) as session:
        principal = await c.e.utu.resolve_agent_principal(
            AgentInstanceKey("tenant", "owner", "agent", "session")
        )
        profile = AgentCognitiveProfile(
            agent_id="agent",
            definition_version="1",
            type="chat",
            role="assistant",
            capabilities=("chat.context", "future.configured"),
        )
        turn = CognitiveTurn(
            id="e",
            tenant_id="tenant",
            conversation_id=c.conversation.id,
            user_id=session.user_id,
            agent_id="agent",
            message="hi",
            correlation_id="e",
            causation_id="e",
        )
        view = AgentHistoryViewV2(agent_id="agent")
        context = CognitiveContext(profile, turn, view, principal, ("chat.context",))
        assert "future.configured" not in context.authorized_capabilities
        with pytest.raises(ValueError):
            CognitiveContext(profile, turn, view, principal, ("llm.generate",))
        with pytest.raises(ValueError):
            CognitiveContext(
                profile, turn.model_copy(update={"user_id": "other"}), view, principal, ()
            )


@pytest.mark.asyncio
async def test_concurrent_admission_no_duplicate_messages_and_tenant_key_isolation(communication):
    c = communication
    c.block[0] = True
    async with c.s.sessions.authenticated(c.s.token) as session:
        running = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(c.entered.wait(), 2)
        duplicate = await c.dispatcher.dispatch(session, c.event)
        other = await c.dispatcher.dispatch(
            session, c.event.model_copy(update={"event_id": "other"})
        )
        assert duplicate["code"] == "pending" and other["code"] == "busy"
        c.release.set()
        await running
        await c.dispatcher.dispatch(session, c.event)
    rows = await c.repository.read_messages("tenant", c.conversation.id)
    assert len(rows) == 2 and len({m.id for m in rows}) == 2
    assert c.repository._keys("tenant", c.conversation.id) != c.repository._keys(
        "other", c.conversation.id
    )
    assert await c.repository.read_messages("other", c.conversation.id) == ()


@pytest.mark.asyncio
async def test_membership_change_during_history_read_fails_closed(communication):
    c = communication
    await append(c, 0)
    original = c.repository.read_messages

    async def read_and_remove(*args, **kwargs):
        rows = await original(*args, **kwargs)
        await c.s.store._repo.redis.set(
            c.repository._keys("tenant", c.conversation.id)[0],
            c.conversation.model_copy(update={"member_user_ids": ("other",)}).model_dump_json(),
        )
        return rows

    c.repository.read_messages = read_and_remove
    async with c.s.sessions.authenticated(c.s.token) as session:
        with pytest.raises(EventPermissionError):
            await c.dispatcher.history.page(session, c.conversation.id)


@pytest.mark.asyncio
async def test_cancel_during_admission_settles_owned_fence(communication):
    c = communication
    original = c.repository.begin
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def paused_begin(*args):
        result = await original(*args)
        admitted.set()
        await release.wait()
        return result

    c.repository.begin = paused_begin
    async with c.s.sessions.authenticated(c.s.token) as session:
        task = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(admitted.wait(), 2)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert await c.repository.read_messages("tenant", c.conversation.id) == ()
    status, response = await original("tenant", "owner", c.event)
    assert status == "replay" and response["code"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_submitted_commit_drains_atomic_write(communication):
    c = communication
    from src.domains.agentverse.repositories.conversation_v2 import _FINISH

    original = c.repository._eval
    submitted = asyncio.Event()
    release = asyncio.Event()

    async def paused_eval(script, keys, args):
        if script == _FINISH:
            submitted.set()
            await release.wait()
        return await original(script, keys, args)

    c.repository._eval = paused_eval
    async with c.s.sessions.authenticated(c.s.token) as session:
        task = asyncio.create_task(c.dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(submitted.wait(), 2)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert len(await c.repository.read_messages("tenant", c.conversation.id)) == 2
    assert (await c.repository.begin("tenant", "owner", c.event))[0] == "replay"
