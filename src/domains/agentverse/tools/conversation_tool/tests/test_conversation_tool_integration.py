"""CONVERSATION-0.1A over real (disposable) Redis, UTU, CPBX and the V2 dispatcher.

Composition (Tool attachment or reviewed compatibility) and capability authorization
(existing UTU grants) are independent; both are required, neither implies the other.
Fake model only.
"""

import dataclasses

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.conversational_composition_v2 import ConversationGate
from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.agents.roles.thoth.spec import role_spec as thoth_role
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.registries import role_registry_instance
from src.domains.agentverse.tests.communication.test_cognitive_response_v2 import (
    communication,  # noqa: F401 - fixture re-export
    response_env,  # noqa: F401 - fixture re-export
    session_env,  # noqa: F401 - fixture re-export
    store,  # noqa: F401 - fixture re-export
)


def attach(r, **config):
    r.definition["tools"] = [{"key": "conversation", "config": config}]


async def grant(r, *signals):
    c = r.c
    policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants(list(signals))})
    await c.e.utu.install_policy(
        "tenant", expected=await c.s.store.current_policy("tenant"), authorization=policy
    )


async def turn(r, name, mode):
    c = r.c
    event = c.event.model_copy(update={"event_id": name, "response_mode": mode})
    async with c.s.sessions.authenticated(c.s.token) as session:
        return await c.dispatcher.dispatch(session, event)


async def transcript(r):
    return [m.kind for m in await r.c.repository.read_messages("tenant", r.c.conversation.id)]


@pytest.mark.asyncio
async def test_compatibility_preserves_existing_conversation(response_env):  # noqa: F811
    r = response_env  # An existing chat definition (effective role ``general``), no Tool.
    assert "tools" not in r.definition
    assert (await turn(r, "llm-1", "llm"))["type"] == "chat.response"
    assert (await turn(r, "ctx-1", "context"))["type"] == "chat.context"
    assert r.repo.get_response_async.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["llm", "context"])
async def test_existing_grant_without_attachment_fails_closed(response_env, mode):  # noqa: F811
    r = response_env
    r.definition["role_name"] = "writer"  # An ordinary Agent: no Tool, no compatibility.
    result = await turn(r, "turn-1", mode)
    assert (result["type"], result["code"]) == ("event.error", "composition_unavailable")
    r.repo.get_response_async.assert_not_awaited()
    assert await transcript(r) == []
    assert not r.c.calls  # No chat.context provider call either.
    # Journaled once: a retry replays the recorded result (CONV-REC-0.1 unchanged).
    assert await turn(r, "turn-1", mode) == result


@pytest.fixture
def listener():
    """A registered, tool-free test Role that has no compatibility entry."""
    spec = dataclasses.replace(role_spec, key="listener", name="Listener")
    role_registry_instance.register_role_spec(spec)
    try:
        yield spec.key
    finally:
        role_registry_instance.unregister(spec.key)


@pytest.mark.asyncio
async def test_explicit_attachment_composes_an_ordinary_agent(response_env, listener):  # noqa: F811
    r = response_env
    r.definition["role_name"] = listener
    assert (await turn(r, "llm-0", "llm"))["code"] == "composition_unavailable"
    attach(r)
    assert (await turn(r, "llm-1", "llm"))["type"] == "chat.response"
    assert (await turn(r, "ctx-1", "context"))["type"] == "chat.context"


@pytest.mark.asyncio
async def test_conversation_modes_restrict_the_turn(response_env):  # noqa: F811
    r = response_env
    attach(r, modes=["context"])
    result = await turn(r, "llm-1", "llm")
    assert result["code"] == "composition_unavailable"
    r.repo.get_response_async.assert_not_awaited()
    assert (await turn(r, "ctx-1", "context"))["type"] == "chat.context"
    attach(r, modes=["llm"])
    assert (await turn(r, "ctx-2", "context"))["code"] == "composition_unavailable"
    assert (await turn(r, "llm-2", "llm"))["type"] == "chat.response"


@pytest.mark.asyncio
async def test_attachment_without_a_grant_is_denied(response_env):  # noqa: F811
    r = response_env
    attach(r)  # Attached and valid, but the Agent lacks execution authority.
    await grant(r, "chat.context")
    async with r.c.s.sessions.authenticated(r.c.s.token) as session:
        with pytest.raises(EventPermissionError):
            await r.c.dispatcher.dispatch(
                session, r.c.event.model_copy(update={"event_id": "llm-1"})
            )
    assert (await turn(r, "ctx-1", "context"))["type"] == "chat.context"
    await grant(r, "other")
    for mode in ("llm", "context"):
        with pytest.raises(EventPermissionError):
            await turn(r, f"{mode}-2", mode)
    r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_ungranted_compatible_agent_acquires_nothing(response_env):  # noqa: F811
    r = response_env
    # Nabu's shape: chat/general, covered by compatibility, holding no grants.
    r.definition.update(role_name="general", name="Nabu")
    await grant(r, "other")
    installed = await r.c.s.store.current_policy("tenant")
    for mode in ("llm", "context"):
        with pytest.raises(EventPermissionError):
            await turn(r, f"{mode}-1", mode)
    # Compatibility changed no policy revision and conferred no grant.
    assert await r.c.s.store.current_policy("tenant") == installed
    r.repo.get_response_async.assert_not_awaited()
    assert await transcript(r) == []


@pytest.mark.asyncio
async def test_history_policy_only_narrows_what_the_model_sees(response_env):  # noqa: F811
    r = response_env
    assert (await turn(r, "llm-1", "llm"))["type"] == "chat.response"
    attach(r, history={"max_messages": 0})
    assert (await turn(r, "llm-2", "llm"))["type"] == "chat.response"
    messages = r.repo.get_response_async.call_args.kwargs["input"]
    assert [m["role"] for m in messages] == ["user"]
    attach(r)
    assert (await turn(r, "llm-3", "llm"))["type"] == "chat.response"
    messages = r.repo.get_response_async.call_args.kwargs["input"]
    assert {"role": "assistant", "content": "I remember Orion."} in messages


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["general", "thoth"])
async def test_a_new_agent_gets_no_implicit_conversation(response_env, role):  # noqa: F811
    r = response_env
    # The production allowlist (AGENT-DEF-SEC-0.1): only existing, exact identities.
    gate = ConversationGate(definitions=r.c.e.definitions)
    r.service.composition = gate
    r.c.dispatcher.composition = gate
    if role == "thoth":
        role_registry_instance.register_role_spec(thoth_role)
    r.definition["role_name"] = role  # A new chat Agent choosing a matching Type/Role.
    for mode in ("llm", "context"):
        result = await turn(r, f"{mode}-1", mode)
        assert (result["type"], result["code"]) == ("event.error", "composition_unavailable")
    r.repo.get_response_async.assert_not_awaited()
    attach(r)  # Explicit attachment is the only path for a new Agent.
    assert (await turn(r, "llm-2", "llm"))["type"] == "chat.response"
