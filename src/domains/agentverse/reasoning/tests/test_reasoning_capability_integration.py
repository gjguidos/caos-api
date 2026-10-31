"""REASONING-0.1B end to end on the reviewed local policy (disposable Mongo and Redis):
real UTU, CPBX, Enki and human session; a fake structured-output model.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import asyncio
import time

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.creation_drafts.capability import PROPOSE_SCOPE
from src.domains.agentverse.catalog.capability import SCOPE as CATALOG_SCOPE
from src.domains.agentverse.catalog.capability import SIGNAL as CATALOG_SIGNAL
from src.domains.agentverse.dependencies.genesis_local import (
    ENKI,
    GENESIS,
    JOSHUA,
    OWNER,
    TENANT,
    THOTH,
)
from src.domains.agentverse.manuscripts.capability import SCOPES as MANUSCRIPT_SCOPES
from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.reasoning.capability import (
    PROVIDER,
    SCOPE,
    SIGNAL,
    VERSION,
    interpret,
)
from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS
from src.domains.agentverse.reasoning.tests.test_reasoning import (
    KEY,
    FakeModel,
    candidate,
    output,
    request,
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

MODEL = "openai-gpt-6-astra"
NABU = "697e8c4b-171a-b672-c90e-f4b6ca5dd05b"


async def denied_scope(exchange, scoped, signal, target, version):
    client = exchange.bind(scoped)
    try:
        with pytest.raises(EventPermissionError):
            await client.request(signal, {}, target=target, version=version)
    finally:
        client.revoke()
        await client.drain()


@pytest.mark.asyncio
async def test_enki_interprets_only_through_its_scoped_signal(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    policy = av.synaptic_authorization()
    assert {a for (_, a), g in policy._grants.items() if SIGNAL in g.invoke} == {ENKI}
    assert not policy._trusted_lines
    binding = s.registry.resolve(SIGNAL, VERSION)
    assert (binding.provider, binding.version) == (PROVIDER, VERSION)
    # Startup registered the reviewed model check; it rejects a catalog without the model.
    (check,) = [c for c in s.app.state.model_catalog_checks]
    with pytest.raises(ValueError):
        check(ClientFactory({"llm": {}}, {}))
    model = FakeModel(output(candidate("agent_type", "chat", "a chat agent")))
    factory = ClientFactory({"llm": {MODEL: model}}, {"llm": MODEL})
    check(factory)
    s.app.state.client_factory = factory
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        before = await snapshot(s)
        async with in_session(s, client):
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )
            result = await interpret(
                authority, exchange, enki, request(), deadline=time.monotonic() + 30
            )
            assert result["status"] == "interpreted", result
            assert result["task"] == KEY
            assert result["task_fingerprint"] == PUBLISHED_FINGERPRINTS[KEY]
            assert result["usage"]["model_key"] == MODEL
            assert set(result["authorizes"].values()) == {False}
            unpublished = await interpret(
                authority, exchange, enki, request(task="manuscript.answer_extraction@9.0.0")
            )
            assert unpublished["failure"]["code"] == "reasoning.task_unavailable"

            # Cross-scope: Manuscript, Catalog and Draft scopes cannot reach Reasoning,
            # and the Reasoning scope cannot reach them.
            for other in (MANUSCRIPT_SCOPES["answer"], CATALOG_SCOPE, PROPOSE_SCOPE):
                await denied_scope(
                    exchange, await authority.attenuate(enki, other), SIGNAL, PROVIDER, VERSION
                )
            narrowed = await authority.attenuate(enki, SCOPE)
            for signal, target in (
                (MANUSCRIPT_SIGNALS["answer"], "system.manuscript"),
                (CATALOG_SIGNAL, "system.catalog"),
                ("agent.draft.propose", "system.agent_drafts"),
            ):
                await denied_scope(exchange, narrowed, signal, target, "1.0.0")
            # No other principal can obtain the scope.
            for agent in (GENESIS, JOSHUA, THOTH, NABU):
                with pytest.raises(EventPermissionError):
                    other = await authority.resolve_agent_principal(
                        AgentInstanceKey(TENANT, OWNER, agent, session_id)
                    )
                    await authority.attenuate(other, SCOPE)

        # The generic human-driven signal route carries an unscoped permit: denied.
        assert (await client.post(f"/api/v2/agentverse/agents/{ENKI}/awaken")).status_code == 200
        generic = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/signals/{SIGNAL}", json=request()
        )
        assert generic.status_code == 403, generic.text
        # Advisory only: no Agent, approval, creation, audit, draft or Manuscript effect.
        after = await snapshot(s)
        unchanged = ("agents", "approvals", "creations", "logs")
        assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}
        for collection in ("coll_agent_drafts", "coll_agent_draft_proposals", SESSIONS_COLLECTION):
            assert await s.mongo.db[collection].count_documents({}) == 0

        # Logout while the model runs: the completed call releases nothing, and Enki's
        # reasoning authority ends with the session.
        slow = FakeModel(("sleep", 0.3, output(candidate("agent_type", "chat", "a chat agent"))))
        s.app.state.client_factory = ClientFactory({"llm": {MODEL: slow}}, {"llm": MODEL})
        async with in_session(s, client):
            pending = asyncio.create_task(interpret(authority, exchange, enki, request()))
            await asyncio.sleep(0.05)
            assert (await client.post("/api/v2/agentverse/session/logout")).status_code == 204
            with pytest.raises((EventPermissionError, asyncio.CancelledError)):
                await pending
        assert len(slow.calls) == 1
        with pytest.raises(EventPermissionError):
            await interpret(authority, exchange, enki, request())
    finally:
        await client.aclose()
