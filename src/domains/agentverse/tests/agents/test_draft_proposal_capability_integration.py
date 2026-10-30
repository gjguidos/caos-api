"""``agent.draft.propose@1.0.0`` end to end on the reviewed local policy (disposable
Mongo and Redis): real UTU, CPBX, Enki and human session; no LLM.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.capability import (
    PROPOSE_SCOPE,
    ProposalSubmission,
    submit_proposal,
)
from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSALS_COLLECTION
from src.domains.agentverse.dependencies.genesis_local import ENKI, GENESIS, JOSHUA, OWNER, TENANT
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    BASE,
    create_body,
    local_app,  # noqa: F401
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki


def in_session(s, client):
    """Enki's turn runs inside its human's authenticated request, as in production."""
    token = client.headers["Authorization"].removeprefix("Bearer ")
    return s.app.state.genesis_sessions.authenticated(token)


def submission(draft_id, *, key="enki-turn-000000000001", base=1):
    return ProposalSubmission.model_validate(
        {
            "contract": "caos.agent_draft_proposal_submission",
            "schema_version": 1,
            "idempotency_key": key,
            "proposal": {
                "contract": "caos.agent_draft_proposal_request",
                "schema_version": 1,
                "draft_id": draft_id,
                "base_revision": base,
                "changes": {
                    "role_name": "general",
                    "personality_key": "empty_eva",
                    "prompt": "Summarize contracts faithfully.",
                },
                "rationale": "Display-only text that must never reach evidence.",
            },
        }
    )


@pytest.mark.asyncio
async def test_enki_proposes_only_through_its_scope_and_the_human_decides(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    authority, exchange = av.utu_authority(), av.synaptic_exchange()
    # Policy: Enki alone holds the signal; no trusted line was added.
    holders = {
        agent
        for (_, agent), grants in av.synaptic_authorization()._grants.items()
        if "agent.draft.propose" in grants.invoke
    }
    assert holders == {ENKI} and not av.synaptic_authorization()._trusted_lines
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        draft = (
            await client.post(
                BASE,
                json=create_body({"name": "Contract Summarizer", "type": "chat"}),
                headers={"Idempotency-Key": "draft-cap-0000001"},
            )
        ).json()
        before = await snapshot(s)

        async with in_session(s, client):
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )
            receipt = await submit_proposal(
                authority, exchange, enki, submission(draft["draft_id"])
            )
            assert receipt["ok"] and receipt["created"] and receipt["state"] == "pending", receipt
            replay = await submit_proposal(authority, exchange, enki, submission(draft["draft_id"]))
            assert replay == {**receipt, "created": False}

            # Other principals cannot even narrow to the proposal scope.
            for agent in (GENESIS, JOSHUA):
                other = await authority.resolve_agent_principal(
                    AgentInstanceKey(TENANT, OWNER, agent, session_id)
                )
                with pytest.raises(EventPermissionError):
                    await authority.attenuate(other, PROPOSE_SCOPE)

        # The generic human-driven signal route carries an unscoped permit: denied.
        assert (await client.post(f"/api/v2/agentverse/agents/{ENKI}/awaken")).status_code == 200
        generic = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/signals/agent.draft.propose",
            json=submission(draft["draft_id"], key="generic-route-00000001").model_dump(
                mode="json"
            ),
        )
        assert generic.status_code == 403, generic.text
        assert await s.mongo.db[PROPOSALS_COLLECTION].count_documents({}) == 1

        # The human decides; Enki has no decision path.
        path = f"{BASE}/{draft['draft_id']}/proposals/{receipt['proposal_id']}"
        decided = await client.post(
            f"{path}/decision",
            json={
                "contract": "caos.agent_draft_proposal_decision",
                "schema_version": 1,
                "decision": "accept",
                "confirmed": True,
                "expected_revision": 1,
                "fields": ["personality_key", "prompt", "role_name"],
            },
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["draft"]["provenance"]["/prompt"]["origin"] == "proposal"
        (event,) = (
            await s.mongo.db["coll_logs"]
            .find({"event_type": "agent.draft.proposal.accepted"})
            .to_list(None)
        )
        assert event["actor"] == {"principal_id": GENESIS, "user_id": OWNER}
        assert event["metadata"]["proposer_agent_id"] == ENKI
        assert "Display-only" not in str(event) and "Summarize" not in str(event)
        after = await snapshot(s)
        assert after["agents"] == before["agents"] and after["approvals"] == before["approvals"]
        assert after["creations"] == before["creations"]

        # Logout revokes the session: Enki's permit can no longer propose.
        assert (await client.post("/api/v2/agentverse/session/logout")).status_code == 204
        with pytest.raises(EventPermissionError):
            async with in_session(s, client):
                await submit_proposal(
                    authority,
                    exchange,
                    enki,
                    submission(draft["draft_id"], key="after-logout-000001", base=2),
                )
        with pytest.raises(EventPermissionError):  # Even outside a request context.
            await submit_proposal(
                authority,
                exchange,
                enki,
                submission(draft["draft_id"], key="after-logout-000002", base=2),
            )
        assert await s.mongo.db[PROPOSALS_COLLECTION].count_documents({}) == 1
    finally:
        await client.aclose()
