"""MANUSCRIPT-0.1C end to end (disposable Mongo and Redis): Enki binds a Manuscript
session only to the human's own editable draft, through its existing scoped signal.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import pytest

from src.domains.agentverse.agents.creation_drafts.models import DRAFTS_COLLECTION
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.manuscripts.capability import SIGNALS
from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.manuscripts.tests.test_manuscript_capability_integration import (
    command,
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
from src.domains.agentverse.tools.manuscript_tool import ManuscriptTool

DRAFTS = f"/api/v2/agentverse/agents/{ENKI}/creation-drafts"


def create_body(name: str) -> dict:
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {"name": name, "type": "chat"},
    }


@pytest.mark.asyncio
async def test_enki_binds_only_the_humans_own_editable_draft(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    policy = av.synaptic_authorization()
    grants_before = {key: set(g.invoke) for key, g in policy._grants.items()}
    tool = ManuscriptTool()
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        draft = (
            await client.post(
                DRAFTS,
                json=create_body("Contract Summarizer"),
                headers={"Idempotency-Key": "mdr-draft-000000001"},
            )
        ).json()
        drafts_before = await s.mongo.db[DRAFTS_COLLECTION].find({}).to_list(None)
        # A draft of the same human for another source Agent is not Enki's to bind.
        await s.mongo.db[DRAFTS_COLLECTION].insert_one(
            {**drafts_before[0], "_id": "foreign-source-draft", "source_agent_id": "thoth"}
        )
        before = await snapshot(s)
        async with in_session(s, client):
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )

            async def start(key, draft_id):
                return await tool.invoke(
                    authority=authority,
                    exchange=exchange,
                    principal=enki,
                    op="start",
                    command=command("start", idempotency_key=key, draft_id=draft_id),
                )

            bound = await start("mdr-start-00000001", draft["draft_id"])
            assert bound["ok"], bound
            assert bound["session"]["draft_id"] == draft["draft_id"]
            assert (
                bound["session"]["manuscript"]["fingerprint"]
                == AGENT_CREATION_INTERVIEW.fingerprint()
            )
            assert await start("mdr-start-00000001", draft["draft_id"]) == bound  # Replay.
            for key, draft_id in (
                ("mdr-start-00000002", "00000000-0000-4000-8000-00000000dead"),
                ("mdr-start-00000003", "foreign-source-draft"),
            ):
                denied = await start(key, draft_id)
                assert (denied["ok"], denied["code"]) == (False, "session.draft_unavailable")
        # Exactly one session was bound; no draft, grant, Agent or approval changed.
        assert await s.mongo.db[SESSIONS_COLLECTION].count_documents({}) == 1
        drafts_after = (
            await s.mongo.db[DRAFTS_COLLECTION]
            .find({"_id": {"$ne": "foreign-source-draft"}})
            .to_list(None)
        )
        assert drafts_after == drafts_before
        assert {key: set(g.invoke) for key, g in policy._grants.items()} == grants_before
        assert {a for (_, a), g in policy._grants.items() if SIGNALS["start"] in g.invoke} == {ENKI}
        after = await snapshot(s)
        unchanged = ("agents", "approvals", "creations", "logs")
        assert {k: after[k] for k in unchanged} == {k: before[k] for k in unchanged}
    finally:
        await client.aclose()
