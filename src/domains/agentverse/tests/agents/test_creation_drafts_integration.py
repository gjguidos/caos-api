"""DRAFT-0.1 on real disposable MongoDB (and, over HTTP, disposable Redis).

Requires ``OPS2_MONGO_INTEGRATION=1``; the HTTP tests also need ``UTU_TEST_REDIS_URL``.
Every database is a generated ``caos_test_*`` database dropped afterwards.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    REVISIONS_COLLECTION,
    AgentDraftRevision,
    FieldProvenance,
    draft_id,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_review.models import APPROVALS_COLLECTION
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
local_app = genesis_tests.local_app


@pytest_asyncio.fixture
async def mongo():
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo draft tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    async with isolated_mongo_repository("drafts") as infrastructure:
        yield infrastructure


def revision(parent: AgentDraftRevision | None, content: dict, *, tenant=TENANT, owner=OWNER):
    number = 1 if parent is None else parent.revision + 1
    return AgentDraftRevision.build(
        draft_id=draft_id(tenant, owner, ENKI, "integration-key-0001")
        if parent is None
        else parent.draft_id,
        revision=number,
        tenant_id=tenant,
        owner_subject_id=owner,
        source_agent_id=ENKI,
        status="editing",
        content=content,
        provenance={"/" + k: FieldProvenance(origin="user", revision=number) for k in content},
        creation_request_digest="1" * 64,
        parent_digest=None if parent is None else parent.revision_digest,
        created_at=T0,
        committed_at=T0 + timedelta(seconds=number),
    )


@pytest.mark.asyncio
async def test_real_mongo_commits_one_writer_per_revision_without_lost_writes(mongo):
    repository = MongoAgentDraftRepository(mongo)
    first, committed = await repository.commit(revision(None, {"name": "Draft"}))
    assert committed
    contenders = [revision(first, {"name": "Draft", "prompt": f"Writer {n}"}) for n in range(16)]
    results = await asyncio.gather(*[repository.commit(record) for record in contenders])
    winners = [stored for stored, ok in results if ok]
    assert len(winners) == 1
    # Every loser sees the single committed revision, never its own content.
    assert {stored.revision_digest for stored, _ in results} == {winners[0].revision_digest}
    assert (await repository.current(TENANT, first.draft_id)) == winners[0]
    revisions = await (await mongo.get_collection(REVISIONS_COLLECTION)).count_documents({})
    assert revisions == 2


@pytest.mark.asyncio
async def test_real_mongo_snapshots_persist_across_instances_and_stay_immutable(mongo):
    writer = MongoAgentDraftRepository(mongo)
    head = (await writer.commit(revision(None, {"name": "Draft"})))[0]
    collection = await mongo.get_collection(REVISIONS_COLLECTION)
    first = await collection.find_one({"_id": f"{head.draft_id}:1"})
    chain = [head]
    for n in range(2, 6):
        head = (await writer.commit(revision(head, {"name": "Draft", "prompt": f"v{n}"})))[0]
        chain.append(head)
    reader = MongoAgentDraftRepository(mongo)  # A fresh instance, as after a restart.
    assert await reader.current(TENANT, head.draft_id) == chain[-1]
    for n, expected in enumerate(chain, start=1):
        assert await reader.revision(TENANT, head.draft_id, n) == expected
    assert await collection.find_one({"_id": f"{head.draft_id}:1"}) == first
    # A conflicting re-commit of an existing revision never replaces it.
    rewrite = revision(chain[0], {"name": "Rewritten"})
    stored, committed = await reader.commit(rewrite)
    assert not committed and stored == chain[1]


@pytest.mark.asyncio
async def test_real_mongo_isolates_tenants_and_rolls_projections_forward(mongo):
    repository = MongoAgentDraftRepository(mongo)
    first = (await repository.commit(revision(None, {"name": "Draft"})))[0]
    assert await repository.current("other-tenant", first.draft_id) is None
    assert await repository.revision("other-tenant", first.draft_id, 1) is None
    second = revision(first, {"name": "Draft", "prompt": "Committed"})
    revisions = await mongo.get_collection(REVISIONS_COLLECTION)
    # Simulate a crash after the commit point and before the projection moved.
    from src.domains.agentverse.agents.creation_drafts.repository import (
        revision_key,
        to_document,
    )

    await revisions.insert_one(to_document(second, revision_key(second.draft_id, 2)))
    drafts = await mongo.get_collection(DRAFTS_COLLECTION)
    assert (await drafts.find_one({"_id": first.draft_id}))["revision"] == 1
    assert await repository.current(TENANT, first.draft_id) == second
    assert (await drafts.find_one({"_id": first.draft_id}))["revision"] == 2


# --- HTTP over the full local Genesis stack -------------------------------------------------

BASE = f"/api/v2/agentverse/agents/{ENKI}/creation-drafts"


def create_body(creation):
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": creation,
    }


def patch(expected, changes=None, remove=()):
    return {
        "contract": "caos.agent_draft_update",
        "schema_version": 1,
        "expected_revision": expected,
        "changes": changes or {},
        "remove": list(remove),
    }


async def snapshot(s):
    db = s.mongo.db
    return {
        "agents": await db["coll_agents"].find({}, {"_id": 0}).to_list(length=None),
        "approvals": await db[APPROVALS_COLLECTION].count_documents({}),
        "creations": await db["coll_agent_creation_operations"].count_documents({}),
        "logs": await db["coll_logs"].count_documents({}),
        "instances": dict(s.av.instance_registry_v2()._map),
    }


@pytest.mark.asyncio
async def test_http_drafts_are_durable_owned_cas_and_effect_free(local_app):
    s = await with_enki(local_app)
    before = await snapshot(s)
    client = await creator(s)
    try:
        partial = {"name": "Contract Summarizer", "type": "chat"}
        made = await client.post(
            BASE, json=create_body(partial), headers={"Idempotency-Key": "draft-http-000001"}
        )
        assert made.status_code == 201, made.text
        draft = made.json()
        path = f"{BASE}/{draft['draft_id']}"
        assert draft["revision"] == 1 and draft["draft"]["creation"] == partial
        # Simultaneous saves from the same revision: exactly one commits.
        responses = await asyncio.gather(
            *[client.patch(path, json=patch(1, {"prompt": f"P{n}"})) for n in range(6)]
        )
        codes = sorted(r.status_code for r in responses)
        assert codes == [200, 409, 409, 409, 409, 409], [r.text for r in responses]
        assert {
            r.json()["detail"]["current_revision"] for r in responses if r.status_code == 409
        } == {2}
        completed = await client.patch(
            path,
            json=patch(
                2,
                {
                    "role_name": "general",
                    "personality_key": "empty_eva",
                    "prompt": "Summarize contracts faithfully.",
                },
            ),
        )
        assert completed.status_code == 200 and completed.json()["revision"] == 3
        # The stored draft is exactly what G3.3 reviews; review is still a separate call.
        review = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/creation-draft/validate",
            json=completed.json()["draft"],
        )
        assert review.json()["eligibility"] == {"eligible": True, "reasons": []}, review.text
        assert (await client.get(f"{path}/revisions/1")).json() == draft
    finally:
        await client.aclose()

    # A new session of the same human reads the same durable draft.
    client = await creator(s)
    try:
        current = (await client.get(path)).json()
        assert current["revision"] == 3 and current == completed.json()
        # A non-creator, and an unbound source, are denied.
        stranger = await creator(s, roles=("caos.admin",))
        try:
            assert (await stranger.get(path)).status_code == 403
        finally:
            await stranger.aclose()
        other = f"/api/v2/agentverse/agents/{OWNER}/creation-drafts/{draft['draft_id']}"
        assert (await client.get(other)).status_code == 403
    finally:
        await client.aclose()

    stored = await s.mongo.db[REVISIONS_COLLECTION].find({}).to_list(length=None)
    assert len(stored) == 3 and {d["owner_subject_id"] for d in stored} == {OWNER}
    assert not any("permit" in key or "token" in key for d in stored for key in d)
    after = await snapshot(s)
    # Validation is read-only; drafts created, approved, activated and logged nothing.
    assert after == before
