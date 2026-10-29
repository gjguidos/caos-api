"""A policy-provisioned Enki is served by the unchanged V2 response path (real Redis/UTU/CPBX)."""

import pytest

from src.domains.agentverse.tests.communication import test_cognitive_response_v2 as responses
from src.domains.agentverse.tests.tools import test_store_provisioned as provisioned

communication = responses.communication
session_env = responses.session_env
store = responses.store
response_env = responses.response_env


@pytest.mark.asyncio
async def test_provisioned_enki_answers_through_cognitive_response_v2(response_env):
    r = response_env
    c = r.c
    s = provisioned.stack()
    result = await provisioned.provision(s, provisioned.body())
    assert result["success"], result
    (document,) = s.persistence.documents
    # The fixture serves this exact stored document for agent "agent" (Enki's pinned ID).
    r.definition.clear()
    r.definition.update(document)
    async with c.s.sessions.authenticated(c.s.token) as session:
        reply = await c.dispatcher.dispatch(session, c.event)
    assert reply["type"] == "chat.response", reply
    assert reply["results"][0]["response"]["agent_id"] == provisioned.ENKI
    assert "Enki" in r.repo.get_response_async.call_args.kwargs["instructions"]
