"""DRAFT-0.2A on real disposable MongoDB (HTTP: also a disposable Redis).

Proposals are submitted through the trusted in-process entry point with a verified
proposer identity, because Enki's ``agent.draft.propose`` capability is not yet
approved or registered. Requires ``OPS2_MONGO_INTEGRATION=1`` (+ ``UTU_TEST_REDIS_URL``).
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.domains.agentverse.agents.creation_drafts.models import REVISIONS_COLLECTION
from src.domains.agentverse.agents.creation_drafts.proposals import (
    PROPOSALS_COLLECTION,
    AgentDraftProposalRequest,
    ProposalAuthor,
)
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    BASE,
    create_body,
    local_app,  # noqa: F401
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki


def enki_author() -> ProposalAuthor:
    """Enki in the owner's session, as the (unapproved) CPBX provider would verify it."""
    permit = SimpleNamespace(
        key=AgentInstanceKey(TENANT, OWNER, ENKI, "integration-session"),
        versions=("policy:1",),
        origin="session",
    )
    return ProposalAuthor.from_permit(permit)  # type: ignore[arg-type]


def decision(body):
    return {
        "contract": "caos.agent_draft_proposal_decision",
        "schema_version": 1,
        "confirmed": True,
        **body,
    }


@pytest.mark.asyncio
async def test_http_decisions_on_real_mongo_apply_once_with_evidence_and_no_effects(local_app):  # noqa: F811
    s = await with_enki(local_app)
    client = await creator(s)
    try:
        made = await client.post(
            BASE,
            json=create_body({"name": "Contract Summarizer", "type": "chat"}),
            headers={"Idempotency-Key": "draft-prop-000001"},
        )
        draft = made.json()
        path = f"{BASE}/{draft['draft_id']}"
        before = await snapshot(s)
        service = s.app.state.agent_draft_proposals
        proposal, created = await service.propose(
            enki_author(),
            AgentDraftProposalRequest.model_validate(
                {
                    "contract": "caos.agent_draft_proposal_request",
                    "schema_version": 1,
                    "draft_id": draft["draft_id"],
                    "base_revision": 1,
                    "changes": {
                        "role_name": "general",
                        "personality_key": "empty_eva",
                        "prompt": "Summarize contracts faithfully.",
                        "name": "Enki's Name",
                    },
                    "rationale": "Display-only rationale that must never reach evidence.",
                }
            ),
            "proposal-integration-0001",
        )
        assert created and proposal.status == "pending"
        # The human renames the Agent before deciding: that field now conflicts.
        renamed = await client.patch(
            path,
            json={
                "contract": "caos.agent_draft_update",
                "schema_version": 1,
                "expected_revision": 1,
                "changes": {"name": "Human Name"},
            },
        )
        assert renamed.json()["revision"] == 2
        view = (await client.get(f"{path}/proposals/{proposal.proposal_id}")).json()
        assert view["conflicting_fields"] == ["name"]
        assert view["applicable_fields"] == ["personality_key", "prompt", "role_name"]
        accept = decision(
            {"decision": "accept", "expected_revision": 2, "fields": view["applicable_fields"]}
        )
        reject = decision({"decision": "reject"})
        responses = await asyncio.gather(
            *[
                client.post(f"{path}/proposals/{proposal.proposal_id}/decision", json=body)
                for body in [accept, reject] * 4
            ]
        )
        ok = [r for r in responses if r.status_code == 200]
        assert {r.status_code for r in responses} <= {200, 409}, [r.text for r in responses]
        states = {r.json()["proposal"]["state"] for r in ok}
        assert len(states) == 1, states
        stored = await s.mongo.db[PROPOSALS_COLLECTION].find_one({"_id": proposal.proposal_id})
        revisions = await s.mongo.db[REVISIONS_COLLECTION].count_documents({})
        logs = (
            await s.mongo.db["coll_logs"]
            .find({"event_type": {"$regex": "^agent.draft"}})
            .to_list(None)
        )
        assert len(logs) == 1
        assert "Display-only rationale" not in str(logs[0]) and "Summarize" not in str(logs[0])
        if stored["status"] == "partially_accepted":
            current = (await client.get(path)).json()
            assert revisions == 3 and current["revision"] == 3
            assert current["draft"]["creation"]["name"] == "Human Name"
            assert current["provenance"]["/prompt"]["origin"] == "proposal"
            assert logs[0]["event_type"] == "agent.draft.proposal.accepted"
            # The accepted draft is reviewable by G3.3 exactly as any human draft.
            review = await client.post(
                f"/api/v2/agentverse/agents/{ENKI}/creation-draft/validate", json=current["draft"]
            )
            assert review.json()["eligibility"]["eligible"] is True, review.text
        else:
            assert stored["status"] == "rejected" and revisions == 2
        after = await snapshot(s)
        # Exactly one Seshat record added; nothing created, approved or activated.
        assert after["logs"] == before["logs"] + 1
        assert {k: v for k, v in after.items() if k != "logs"} == {
            k: v for k, v in before.items() if k != "logs"
        }
    finally:
        await client.aclose()

    # Another session of the same human replays nothing new; a stranger is denied.
    client = await creator(s)
    stranger = await creator(s, roles=("caos.admin",))
    try:
        again = await client.post(
            f"{path}/proposals/{proposal.proposal_id}/decision",
            json=accept if stored["status"] == "partially_accepted" else reject,
        )
        assert again.status_code == 200
        assert (await stranger.get(f"{path}/proposals/{proposal.proposal_id}")).status_code == 403
    finally:
        await client.aclose()
        await stranger.aclose()
