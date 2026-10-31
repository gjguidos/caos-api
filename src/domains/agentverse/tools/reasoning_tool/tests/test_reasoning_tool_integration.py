"""REASONING-0.1C end to end on the reviewed local policy (disposable Mongo and Redis):
the Reasoning Tool reaches the model only through Enki's scoped ``cognitive.interpret``.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import time

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.dependencies.genesis_local import (
    ENKI,
    GENESIS,
    JOSHUA,
    OWNER,
    TENANT,
    THOTH,
)
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.reasoning.capability import SIGNAL
from src.domains.agentverse.reasoning.tests.test_reasoning import (
    KEY,
    FakeModel,
    candidate,
    output,
    request,
)
from src.domains.agentverse.reasoning.tests.test_reasoning_capability_integration import (
    MODEL,
    NABU,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    local_app,  # noqa: F401
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_draft_proposal_capability_integration import (
    in_session,
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki
from src.domains.agentverse.tools.reasoning_tool import ReasoningTool


@pytest.mark.asyncio
async def test_tool_executes_only_through_enkis_governed_capability(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    policy = av.synaptic_authorization()
    # Registering the Tool added no grant or trusted line: Enki remains the only holder.
    assert {a for (_, a), g in policy._grants.items() if SIGNAL in g.invoke} == {ENKI}
    assert not policy._trusted_lines
    # Enki's persisted Tool list is not changed by the Tool's existence.
    enki_doc = await av.db_service().find_by_id(ENKI)
    assert "reasoning" not in {t.get("key") for t in enki_doc.get("tools") or []}

    model = FakeModel(output(candidate("agent_type", "chat", "a chat agent")))
    s.app.state.client_factory = ClientFactory({"llm": {MODEL: model}}, {"llm": MODEL})
    tool = ReasoningTool({"tasks": [KEY]})
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        before = await snapshot(s)
        async with in_session(s, client):
            principal = AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            enki = await authority.resolve_agent_principal(principal)
            context = {"authority": authority, "exchange": exchange, "principal": enki}
            result = await tool.invoke(**context, request=request(), deadline=time.monotonic() + 30)
            assert result["status"] == "interpreted", result
            assert result["usage"]["model_key"] == MODEL
            assert set(result["authorizes"].values()) == {False}
            assert len(model.calls) == 1

            # Unauthorized Tool paths never reach the model.
            forged = await tool.invoke(**context, request={**request(), "model": "x"})
            assert forged["failure"]["code"] == "reasoning.invalid_request"
            with pytest.raises(EventPermissionError, match="V1 is denied"):
                await tool.execute(**request())
            for agent in (GENESIS, JOSHUA, THOTH, NABU):
                with pytest.raises(EventPermissionError):
                    other = await authority.resolve_agent_principal(
                        AgentInstanceKey(TENANT, OWNER, agent, session_id)
                    )
                    # Attaching the Tool to another Agent supplies no grant.
                    await tool.invoke(
                        authority=authority, exchange=exchange, principal=other, request=request()
                    )
            assert len(model.calls) == 1

        # The generic signal route stays denied for the scoped capability.
        assert (await client.post(f"/api/v2/agentverse/agents/{ENKI}/awaken")).status_code == 200
        generic = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/signals/{SIGNAL}", json=request()
        )
        assert generic.status_code == 403, generic.text
        assert len(model.calls) == 1

        # Advisory only: no Agent, approval, creation, audit, draft, Store or Manuscript effect.
        after = await snapshot(s)
        unchanged = ("agents", "approvals", "creations", "logs")
        assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}
        for collection in ("coll_agent_drafts", "coll_agent_draft_proposals", SESSIONS_COLLECTION):
            assert await s.mongo.db[collection].count_documents({}) == 0
        # Without the human session, the Tool cannot execute even for Enki.
        with pytest.raises(EventPermissionError):
            await tool.invoke(**context, request=request())
        assert len(model.calls) == 1
    finally:
        await client.aclose()
