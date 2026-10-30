"""DRAFT-0.2B end to end on disposable Mongo and Redis: server draft → exact-revision
review → approval by reference (freeze) → unchanged G3.4 creation → confirmed
consumption; listing and fork on real MongoDB. No LLM, grant or activation.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import asyncio

import pytest

from src.domains.agentverse.dependencies.genesis_local import ENKI
from src.domains.agentverse.tests.agents.test_agent_creation_integration import (
    create,
    key,
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    BASE,
    create_body,
    local_app,  # noqa: F401
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator, draft
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

CREATE = f"/api/v2/agentverse/agents/{ENKI}/creation-draft/create"


def patch(expected, changes):
    return {
        "contract": "caos.agent_draft_update",
        "schema_version": 1,
        "expected_revision": expected,
        "changes": changes,
    }


def approval(revision, digest, idempotency="draft-approval-integ-01"):
    return {
        "contract": "caos.agent_draft_approval",
        "schema_version": 1,
        "revision": revision,
        "review_digest": digest,
        "decision": "approve",
        "confirmed": True,
        "idempotency_key": idempotency,
    }


@pytest.mark.asyncio
async def test_server_draft_to_governed_creation_and_consumption(local_app):  # noqa: F811
    s = await with_enki(local_app)
    tab_a, tab_b = await creator(s), await creator(s)  # Two tabs, two sessions.
    try:
        made = await tab_a.post(
            BASE,
            json=create_body({"name": "Draft Created"}),
            headers={"Idempotency-Key": "draft-life-00000001"},
        )
        assert made.status_code == 201, made.text
        draft_id = made.json()["draft_id"]
        path = f"{BASE}/{draft_id}"

        # Reload / other tab: the owner's open drafts resume the same server draft.
        listed = (await tab_b.get(BASE, params={"status": "open", "limit": 5})).json()
        assert [d["draft_id"] for d in listed["items"]] == [draft_id]

        # Concurrent saves from two tabs at the same revision: one wins, one conflicts.
        complete = draft()["creation"]
        results = await asyncio.gather(
            tab_a.patch(path, json=patch(1, complete)),
            tab_b.patch(path, json=patch(1, {"prompt": "Tab B prompt"})),
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        loser = next(r for r in results if r.status_code == 409)
        assert loser.json()["detail"]["current_revision"] == 2
        current = (await tab_a.get(path)).json()
        if current["draft"]["creation"].get("prompt") == "Tab B prompt":
            current = (await tab_a.patch(path, json=patch(2, complete))).json()
        revision = current["revision"]

        review = await tab_a.post(f"{path}/revisions/{revision}/review")
        assert review.status_code == 200, review.text
        envelope = review.json()
        assert envelope["eligibility"]["eligible"] and envelope["draft_ref"]["revision"] == revision
        direct = (
            await tab_a.post(
                f"/api/v2/agentverse/agents/{ENKI}/creation-draft/validate",
                json=current["draft"],
            )
        ).json()
        assert direct["review_digest"] == envelope["review_digest"]

        approved = await tab_a.post(
            f"{path}/approval", json=approval(revision, envelope["review_digest"])
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["draft"]["status"] == "approved"
        assert body["draft"]["lifecycle"]["reviewed_revision"] == revision
        frozen = body["draft"]["revision"]
        blocked = await tab_b.patch(path, json=patch(frozen, {"prompt": "Too late"}))
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "draft.approved"

        before = await snapshot(s)
        agents_before = await s.mongo.db["coll_agents"].count_documents({})
        created = await tab_a.post(CREATE, json=create(body["approval"]), headers=key())
        assert created.status_code == 200 and created.json()["state"] == "completed", created.text
        agent_id = created.json()["agent_id"]

        consumed = (await tab_b.get(path)).json()
        assert consumed["status"] == "consumed" and consumed["revision"] == frozen + 1
        assert consumed["lifecycle"]["agent_id"] == agent_id
        assert consumed["content_digest"] == current["content_digest"]
        assert (await tab_a.get(path)).json() == consumed  # Idempotent.
        reopen = await tab_a.post(
            f"{path}/reopen",
            json={
                "contract": "caos.agent_draft_reopen",
                "schema_version": 1,
                "expected_revision": consumed["revision"],
            },
        )
        assert reopen.status_code == 409 and reopen.json()["detail"]["code"] == "draft.consumed"

        # Exactly one sleeping Agent; no grant, approval or activation change.
        assert await s.mongo.db["coll_agents"].count_documents({}) == agents_before + 1
        assert (await s.mongo.db["coll_agents"].find_one({"id": agent_id}))[
            "runtime_version"
        ] == "v2"
        after = await snapshot(s)
        assert after == before

        # Fork: a new editing draft; the consumed original stays read-only.
        fork = await tab_a.post(
            f"{path}/fork",
            json={"contract": "caos.agent_draft_fork", "schema_version": 1, "revision": revision},
            headers={"Idempotency-Key": "draft-fork-00000001"},
        )
        assert fork.status_code == 201, fork.text
        assert fork.json()["forked_from"] == {"draft_id": draft_id, "revision": revision}
        listing = (await tab_a.get(BASE, params={"status": "open"})).json()["items"]
        assert [d["draft_id"] for d in listing] == [fork.json()["draft_id"]]
        everything = (await tab_a.get(BASE, params={"status": "all", "limit": 1})).json()
        assert len(everything["items"]) == 1 and everything["next_cursor"]
        second = (
            await tab_a.get(
                BASE, params={"status": "all", "limit": 1, "cursor": everything["next_cursor"]}
            )
        ).json()
        assert [d["draft_id"] for d in second["items"]] == [draft_id]
        assert (
            await tab_a.get(BASE, params={"status": "open", "cursor": everything["next_cursor"]})
        ).status_code == 422
        indexes = await s.mongo.db["coll_agent_drafts"].index_information()
        assert "draft_owner_recent" in indexes
    finally:
        await tab_a.aclose()
        await tab_b.aclose()


@pytest.mark.asyncio
async def test_reopen_withdraws_an_unconsumed_approval_and_non_creators_are_denied(local_app):  # noqa: F811
    s = await with_enki(local_app)
    client = await creator(s)
    stranger = await creator(s, roles=("caos.admin",))
    try:
        made = await client.post(
            BASE,
            json=create_body(draft()["creation"]),
            headers={"Idempotency-Key": "draft-life-00000002"},
        )
        path = f"{BASE}/{made.json()['draft_id']}"
        envelope = (await client.post(f"{path}/revisions/1/review")).json()
        body = (
            await client.post(
                f"{path}/approval",
                json=approval(1, envelope["review_digest"], "draft-approval-integ-02"),
            )
        ).json()
        reopened = await client.post(
            f"{path}/reopen",
            json={
                "contract": "caos.agent_draft_reopen",
                "schema_version": 1,
                "expected_revision": 2,
            },
        )
        assert reopened.status_code == 200 and reopened.json()["status"] == "editing"
        # The withdrawn approval can no longer create anything.
        refused = await client.post(CREATE, json=create(body["approval"]), headers=key())
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "approval.revoked"
        revoked = await s.mongo.db["coll_logs"].count_documents(
            {"event_type": "agent.creation_review.revoked"}
        )
        assert revoked == 1
        for request in (
            stranger.get(BASE),
            stranger.get(path),
            stranger.post(f"{path}/revisions/1/review"),
            stranger.get(f"{path}/proposals"),
        ):
            assert (await request).status_code == 403
    finally:
        await client.aclose()
        await stranger.aclose()
