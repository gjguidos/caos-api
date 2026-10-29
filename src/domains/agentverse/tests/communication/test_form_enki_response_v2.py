"""Conversation for an approved Form composition, with unchanged UTU enforcement."""

import pytest

from src.domains.agentverse.agents.roles.enki.spec import role_spec
from src.domains.agentverse.registries import role_registry_instance
from src.domains.agentverse.tests.communication import test_cognitive_response_v2 as responses

communication = responses.communication
session_env = responses.session_env
store = responses.store
response_env = responses.response_env


@pytest.mark.asyncio
@pytest.mark.parametrize("role,allowed", [("enki", True), ("general", False), ("crawler", False)])
async def test_conversational_form_requires_approved_composition(response_env, role, allowed):
    r = response_env
    role_registry_instance.register_role_spec(role_spec)
    r.definition.update(type="form", role_name=role, name="Enki")
    async with r.c.s.sessions.authenticated(r.c.s.token) as session:
        result = await r.c.dispatcher.dispatch(session, r.c.event)
    if allowed:
        assert result["type"] == "chat.response", result
        assert "Agent type: form" in r.repo.get_response_async.call_args.kwargs["instructions"]
        assert "Enki" in r.repo.get_response_async.call_args.kwargs["instructions"]
    else:
        assert result["type"] == "event.error" and result["code"] == "composition_unavailable"
        r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_form_type_never_grants_chat_response(response_env):
    from src.base.events.v2.contracts import EventPermissionError
    from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
    from src.domains.agentverse.ncsc.models import SynapticGrants

    r = response_env
    role_registry_instance.register_role_spec(role_spec)
    r.definition.update(type="form", role_name="enki", name="Enki")
    policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants(["chat.context"])})
    await r.c.e.utu.install_policy(
        "tenant", expected=await r.c.s.store.current_policy("tenant"), authorization=policy
    )
    async with r.c.s.sessions.authenticated(r.c.s.token) as session:
        with pytest.raises(EventPermissionError):
            await r.c.dispatcher.dispatch(session, r.c.event)
    r.repo.get_response_async.assert_not_awaited()
