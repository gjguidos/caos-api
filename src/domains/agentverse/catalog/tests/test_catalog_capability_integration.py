"""CATALOG-0.1A end to end on the reviewed local policy (disposable Mongo and Redis):
real UTU, CPBX, Enki and human session; no LLM.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.catalog import CatalogListRequest, CatalogRequester
from src.domains.agentverse.catalog.capability import (
    PROVIDER,
    SCOPE,
    SIGNAL,
    VERSION,
    project_catalog,
)
from src.domains.agentverse.dependencies.genesis_local import (
    ENKI,
    GENESIS,
    JOSHUA,
    OWNER,
    TENANT,
)
from src.domains.agentverse.manuscripts.capability import SCOPES as MANUSCRIPT_SCOPES
from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
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


def command(operation, **request):
    return {
        "contract": "caos.catalog.project",
        "schema_version": 1,
        "operation": operation,
        "request": request,
    }


@pytest.mark.asyncio
async def test_enki_projects_its_humans_catalog_only_through_the_scoped_signal(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    policy = av.synaptic_authorization()
    holders = {agent for (_, agent), g in policy._grants.items() if SIGNAL in g.invoke}
    assert holders == {ENKI}
    assert not policy._trusted_lines
    binding = s.registry.resolve(SIGNAL, VERSION)
    assert (binding.provider, binding.version) == (PROVIDER, VERSION)
    catalog = s.app.state.resource_catalog
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        before = await snapshot(s)
        async with in_session(s, client):
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )
            human = await authority.resource_requester(enki.permit)
            expected = CatalogRequester(user=human, source_agent_id=ENKI)

            for kind in ("agent_type", "role", "soul", "tool", "rulepack", "manuscript"):
                served = await project_catalog(
                    authority, exchange, enki, command("list", kind=kind, limit=100)
                )
                own = await catalog.list(expected, CatalogListRequest(kind=kind, limit=100))
                assert served["ok"] and served["result"] == own.model_dump(mode="json"), kind
                assert all(
                    e["decisions"]["execution_authorization"] == "not_evaluated"
                    for e in served["result"]["entries"]
                )
            # The reviewed gateway binding (tenant, human, Enki) makes the published
            # Manuscript visible, with its pinned fingerprint.
            interview = await project_catalog(
                authority,
                exchange,
                enki,
                command("resolve", kind="manuscript", value="agent_creation_interview@1.0.0"),
            )
            ref = interview["result"]["entry"]["ref"]
            assert ref["fingerprint"] == AGENT_CREATION_INTERVIEW.fingerprint()
            chat = await project_catalog(
                authority,
                exchange,
                enki,
                command("resolve", kind="role", value="general", candidate={"type": "chat"}),
            )
            assert chat["result"]["status"] == "resolved"
            visible = {
                e["ref"]["key"]
                for e in (
                    await project_catalog(
                        authority, exchange, enki, command("list", kind="tool", limit=100)
                    )
                )["result"]["entries"]
            }
            # A registered Tool the human cannot see (internal/system) exists in the registry.
            protected = next(
                k
                for k in s.app.state.resource_catalog._registries["tool"].keys()  # noqa: SIM118
                if k not in visible
            )
            hidden = await project_catalog(
                authority, exchange, enki, command("resolve", kind="tool", value=protected)
            )
            unknown = await project_catalog(
                authority, exchange, enki, command("resolve", kind="tool", value="no_such_tool")
            )
            assert hidden == unknown and hidden["result"]["status"] == "unavailable"
            forged = await project_catalog(
                authority, exchange, enki, {**command("list", kind="tool"), "tenant_id": "x"}
            )
            assert forged == {
                "contract": "caos.catalog.project_result",
                "ok": False,
                "code": "catalog.invalid_request",
                "retryable": False,
            }

            # Another capability's scope cannot reach the catalog provider at CPBX.
            scoped = await authority.attenuate(enki, MANUSCRIPT_SCOPES["status"])
            foreign = exchange.bind(scoped)
            try:
                with pytest.raises(EventPermissionError):
                    await foreign.request(
                        SIGNAL, command("list", kind="tool"), target=PROVIDER, version=VERSION
                    )
            finally:
                foreign.revoke()
                await foreign.drain()
            # The catalog scope cannot reach a Manuscript signal either.
            narrowed = exchange.bind(await authority.attenuate(enki, SCOPE))
            try:
                with pytest.raises(EventPermissionError):
                    await narrowed.request(
                        MANUSCRIPT_SIGNALS["status"],
                        {"contract": "caos.manuscript_session.status", "schema_version": 1},
                        target="system.manuscript",
                        version="1.0.0",
                    )
            finally:
                narrowed.revoke()
                await narrowed.drain()
            # No other principal can narrow to the catalog scope.
            for agent in (GENESIS, JOSHUA):
                other = await authority.resolve_agent_principal(
                    AgentInstanceKey(TENANT, OWNER, agent, session_id)
                )
                with pytest.raises(EventPermissionError):
                    await authority.attenuate(other, SCOPE)
                with pytest.raises(EventPermissionError):
                    await project_catalog(authority, exchange, other, command("list", kind="tool"))

        # The generic human-driven signal route carries an unscoped permit: denied.
        assert (await client.post(f"/api/v2/agentverse/agents/{ENKI}/awaken")).status_code == 200
        generic = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/signals/{SIGNAL}",
            json=command("list", kind="tool"),
        )
        assert generic.status_code == 403, generic.text
        # Read-only: no Agent, approval, creation, audit, draft or Manuscript effect.
        after = await snapshot(s)
        unchanged = ("agents", "approvals", "creations", "logs")
        assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}
        for collection in ("coll_agent_drafts", "coll_agent_draft_proposals", SESSIONS_COLLECTION):
            assert await s.mongo.db[collection].count_documents({}) == 0

        # Logout revokes the live session: Enki's catalog authority ends with it.
        assert (await client.post("/api/v2/agentverse/session/logout")).status_code == 204
        with pytest.raises(EventPermissionError):
            await project_catalog(authority, exchange, enki, command("list", kind="tool"))
    finally:
        await client.aclose()
