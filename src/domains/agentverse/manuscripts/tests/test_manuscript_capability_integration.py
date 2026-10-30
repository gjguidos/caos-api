"""MANUSCRIPT-0.1A end to end on the reviewed local policy (disposable Mongo and Redis):
real UTU, CPBX, Enki and human session; no LLM.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.dependencies.genesis_local import ENKI, GENESIS, JOSHUA, OWNER, TENANT
from src.domains.agentverse.manuscripts.capability import (
    OPERATIONS,
    PROVIDER,
    SCOPES,
    SIGNALS,
    VERSION,
)
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
from src.domains.agentverse.tools.manuscript_tool import ManuscriptTool
from src.domains.agentverse.tools.spec import AgentToolRef

STEPS = [
    {"purpose": "Summarize legal contracts for the sales team."},
    {"name": "Contract Summarizer"},
    {"agent_type": "chat", "role": "general", "soul": "empty_eva"},
    {"capabilities_reviewed": True},
    {"prompt": "Summarize contracts faithfully and flag risks.", "visibility": "private"},
    {"review_ready": True},
]


def command(op, **fields):
    return {"contract": f"caos.manuscript_session.{op}", "schema_version": 1, **fields}


@pytest.mark.asyncio
async def test_enki_runs_a_manuscript_only_through_its_six_scoped_signals(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    # A normal AgentToolRef selects configuration; it cannot supply execution grants.
    ref = AgentToolRef(
        key="manuscript", config={"default_manuscript": "agent_creation_interview@1.0.0"}
    )
    tool = ManuscriptTool(ref.config)
    policy = av.synaptic_authorization()
    for signal in SIGNALS.values():
        holders = {agent for (_, agent), g in policy._grants.items() if signal in g.invoke}
        assert holders == {ENKI}, signal
        binding = s.registry.resolve(signal)
        assert (binding.provider, binding.version) == (PROVIDER, VERSION)
    assert not policy._trusted_lines
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        before = await snapshot(s)
        async with in_session(s, client):
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )

            async def run(op, **fields):
                return await tool.invoke(
                    authority=authority,
                    exchange=exchange,
                    principal=enki,
                    op=op,
                    command=command(op, **fields),
                )

            started = await run(
                "start",
                idempotency_key="enki-manuscript-000001",
            )
            assert started["ok"], started
            session = started["session"]
            assert session["manuscript"]["fingerprint"] == AGENT_CREATION_INTERVIEW.fingerprint()
            sid, revision = session["session_id"], session["revision"]
            invented = await run(
                "answer",
                session_id=sid,
                expected_revision=revision,
                answers={"role": "not_a_real_role"},
                request_key="enki-manuscript-bad-01",
            )
            assert invented["outcome"]["rejected"] == {"role": "reference.unavailable"}
            for index, answers in enumerate(STEPS):
                result = await run(
                    "answer",
                    session_id=sid,
                    expected_revision=revision,
                    answers=answers,
                    request_key=f"enki-manuscript-step-{index:02d}",
                )
                assert result["ok"] and result["outcome"]["status"] == "accepted", result
                revision = result["session"]["revision"]
            replay = await run(
                "answer",
                session_id=sid,
                expected_revision=revision - 1,
                answers=STEPS[-1],
                request_key=f"enki-manuscript-step-{len(STEPS) - 1:02d}",
            )
            assert replay["session"]["revision"] == revision  # Applied once.
            corrected = await run(
                "correct",
                session_id=sid,
                expected_revision=revision,
                stage="configuration",
                answers={"visibility": "public"},
                request_key="enki-manuscript-fix-001",
            )
            assert corrected["ok"] and corrected["session"]["answers"]["visibility"] == "public"
            revision = corrected["session"]["revision"]
            assert (await run("status", session_id=sid))["session"]["ready_to_complete"]
            done = await run(
                "complete",
                session_id=sid,
                expected_revision=revision,
                request_key="enki-manuscript-done-01",
            )
            assert done["ok"] and done["session"]["status"] == "completed"
            fresh = await run("restart", session_id=sid, request_key="enki-manuscript-rst-01")
            assert fresh["ok"] and fresh["session"]["restarted_from"] == sid

            # Every signal refuses another signal's scope at CPBX, before the provider.
            for op in OPERATIONS:
                for other in OPERATIONS:
                    if other == op:
                        continue
                    scoped = await authority.attenuate(enki, SCOPES[other])
                    foreign = exchange.bind(scoped)
                    try:
                        with pytest.raises(EventPermissionError):
                            await foreign.request(
                                SIGNALS[op],
                                command(op, session_id=sid),
                                target=PROVIDER,
                                version=VERSION,
                            )
                    finally:
                        foreign.revoke()
                        await foreign.drain()
            # Other principals cannot narrow to any Manuscript scope.
            for agent in (GENESIS, JOSHUA):
                other = await authority.resolve_agent_principal(
                    AgentInstanceKey(TENANT, OWNER, agent, session_id)
                )
                for op in OPERATIONS:
                    with pytest.raises(EventPermissionError):
                        await authority.attenuate(other, SCOPES[op])
                with pytest.raises(EventPermissionError):
                    await tool.invoke(
                        authority=authority,
                        exchange=exchange,
                        principal=other,
                        op="status",
                        command=command("status", session_id=sid),
                    )

        # The generic human-driven signal route carries an unscoped permit: denied.
        assert (await client.post(f"/api/v2/agentverse/agents/{ENKI}/awaken")).status_code == 200
        generic = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/signals/manuscript.session.status",
            json=command("status", session_id=sid),
        )
        assert generic.status_code == 403, generic.text
        after = await snapshot(s)
        unchanged = ("agents", "approvals", "creations", "logs")
        assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}
        assert await s.mongo.db["coll_agent_drafts"].count_documents({}) == 0
        assert await s.mongo.db["coll_agent_draft_proposals"].count_documents({}) == 0
        assert await s.mongo.db[SESSIONS_COLLECTION].count_documents({}) == 2

        # Logout revokes the live session: Enki's Manuscript authority ends with it.
        assert (await client.post("/api/v2/agentverse/session/logout")).status_code == 204
        with pytest.raises(EventPermissionError):
            await tool.invoke(
                authority=authority,
                exchange=exchange,
                principal=enki,
                op="status",
                command=command("status", session_id=sid),
            )
    finally:
        await client.aclose()
