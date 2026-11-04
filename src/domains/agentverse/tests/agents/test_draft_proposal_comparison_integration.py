"""COORD-0.3 proposal review integrity on real disposable MongoDB and Redis (HTTP).

The owner's proposal view shows, per proposed field, the value at the proposal's own base
revision, the proposed value (an acceptance replaces the whole field) and the current
value, with an entry-level view of collection replacements. Decisions are unchanged.
Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import pytest

from src.domains.agentverse.agents.creation_drafts.proposals import AgentDraftProposalRequest
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    BASE,
    create_body,
    local_app,  # noqa: F401
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_draft_proposals_integration import (
    decision,
    enki_author,
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

ORIGINAL = {
    "name": "Contract Summarizer",
    "type": "chat",
    "prompt": "Old prompt for contracts.",
    "tools": [
        {"key": "rag", "config": {"top_k": 3}},
        {"key": "date_tool", "config": {}},
        {"key": "math_tool", "config": {}},
    ],
    "workflows": ["wf_a", "wf_b"],
}
PROPOSED = {
    "prompt": "New prompt for contracts.",  # Scalar replaced.
    "tools": [
        {"key": "calc", "config": {}},  # Added.
        {"key": "date_tool", "config": {"tz": "UTC"}},  # Configuration changed.
        {"key": "rag", "config": {"top_k": 3}},  # Retained unchanged (now reordered).
    ],  # math_tool is not listed: accepting the field removes it.
    "workflows": ["wf_b", "wf_a"],  # Same entries, new order.
}


def patch(expected, changes):
    return {
        "contract": "caos.agent_draft_update",
        "schema_version": 1,
        "expected_revision": expected,
        "changes": changes,
    }


async def propose(s, draft_id, changes, key):
    proposal, created = await s.app.state.agent_draft_proposals.propose(
        enki_author(),
        AgentDraftProposalRequest.model_validate(
            {
                "contract": "caos.agent_draft_proposal_request",
                "schema_version": 1,
                "draft_id": draft_id,
                "base_revision": 1,
                "changes": changes,
            }
        ),
        key,
    )
    assert created
    return proposal


def rows(view):
    return {row["field"]: row for row in view["comparison"]}


@pytest.mark.asyncio
async def test_the_owner_sees_base_proposed_and_current_values_of_every_field(local_app):  # noqa: F811
    s = await with_enki(local_app)
    client = await creator(s)
    stranger = await creator(s, roles=("caos.admin",))
    try:
        made = await client.post(
            BASE, json=create_body(ORIGINAL), headers={"Idempotency-Key": "compare-draft-0001"}
        )
        assert made.status_code == 201, made.text
        draft_id = made.json()["draft_id"]
        path = f"{BASE}/{draft_id}"
        proposal = await propose(s, draft_id, PROPOSED, "compare-proposal-0001")
        url = f"{path}/proposals/{proposal.proposal_id}"

        view = (await client.get(url)).json()
        # Existing properties are unchanged; the comparison is an addition.
        assert view["changes"] == PROPOSED and view["remove"] == []
        assert view["applicable_fields"] == ["prompt", "tools", "workflows"]
        compared = rows(view)
        assert set(compared) == {"prompt", "tools", "workflows"}
        prompt = compared["prompt"]
        assert prompt["base"] == {"state": "present", "value": ORIGINAL["prompt"]}
        assert prompt["proposed"] == {"state": "present", "value": PROPOSED["prompt"]}
        assert prompt["current"] == prompt["base"] and prompt["conflicting"] is False
        assert "items" not in prompt

        tools = compared["tools"]
        assert tools["base"]["value"] == ORIGINAL["tools"]
        assert tools["proposed"]["value"] == PROPOSED["tools"]
        assert tools["items"] == {
            "state": "available",
            "added": [{"key": "calc", "config": {}}],
            "removed": [{"key": "math_tool", "config": {}}],
            "retained": [
                {"entry": "date_tool", "changed": True},
                {"entry": "rag", "changed": False},
            ],
            "reordered": True,
        }
        assert compared["workflows"]["items"] == {
            "state": "available",
            "added": [],
            "removed": [],
            "retained": [{"entry": "wf_b"}, {"entry": "wf_a"}],
            "reordered": True,
        }
        # The listing carries the same comparison.
        listed = (await client.get(f"{path}/proposals")).json()
        assert listed["items"][0]["comparison"] == view["comparison"]

        # The draft advances after submission: base values stay those of the proposal's
        # base revision; the current value is reported separately; prompt now conflicts.
        renamed = await client.patch(path, json=patch(1, {"name": "Human Name"}))
        assert renamed.status_code == 200, renamed.text
        assert rows((await client.get(url)).json())["prompt"]["base"] == prompt["base"]
        edited = await client.patch(path, json=patch(2, {"prompt": "Human prompt."}))
        assert edited.status_code == 200, edited.text
        later = (await client.get(url)).json()
        later_prompt = rows(later)["prompt"]
        assert later_prompt["base"] == prompt["base"]
        assert later_prompt["current"] == {"state": "present", "value": "Human prompt."}
        assert later_prompt["conflicting"] is True
        assert later["conflicting_fields"] == ["prompt"]
        assert rows(later)["tools"] == {**tools, "current": tools["base"]}

        # Not visible to a non-creator (and so never its base values).
        for target in (url, f"{path}/proposals"):
            assert (await stranger.get(target)).status_code in (403, 404)

        # Acceptance applies only the selected fields, the Tools field as a whole.
        accepted = await client.post(
            f"{url}/decision",
            json=decision({"decision": "accept", "expected_revision": 3, "fields": ["tools"]}),
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        current = body["draft"]["draft"]["creation"]
        assert current["tools"] == PROPOSED["tools"]  # math_tool removed by the acceptance.
        assert current["prompt"] == "Human prompt." and current["workflows"] == ["wf_a", "wf_b"]
        assert rows(body["proposal"])["tools"]["base"] == tools["base"]

        # Rejection leaves the draft unchanged.
        second = await propose(s, draft_id, {"type": "assistant"}, "compare-proposal-0002")
        before = (await client.get(path)).json()
        rejected = await client.post(
            f"{path}/proposals/{second.proposal_id}/decision",
            json=decision({"decision": "reject"}),
        )
        assert rejected.status_code == 200, rejected.text
        after = (await client.get(path)).json()
        assert (after["revision"], after["draft"]) == (before["revision"], before["draft"])
        assert rows(rejected.json()["proposal"])["type"]["base"] == {
            "state": "present",
            "value": "chat",
        }
    finally:
        await client.aclose()
        await stranger.aclose()


@pytest.mark.asyncio
async def test_an_unverifiable_base_value_is_reported_unavailable(local_app):  # noqa: F811
    s = await with_enki(local_app)
    client = await creator(s)
    try:
        made = await client.post(
            BASE, json=create_body(ORIGINAL), headers={"Idempotency-Key": "compare-draft-0002"}
        )
        draft_id = made.json()["draft_id"]
        proposal = await propose(s, draft_id, PROPOSED, "compare-proposal-0003")
        service = s.app.state.agent_draft_proposals

        async def unreadable(_proposal):
            return None

        service.base_of = unreadable  # The base revision cannot be read (outage).
        view = (await client.get(f"{BASE}/{draft_id}/proposals/{proposal.proposal_id}")).json()
        assert {row["base"]["state"] for row in view["comparison"]} == {"unavailable"}
        assert rows(view)["tools"]["items"] == {"state": "unavailable"}
        assert view["changes"] == PROPOSED  # The proposal itself is still shown.
    finally:
        await client.aclose()
