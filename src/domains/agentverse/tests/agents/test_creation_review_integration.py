"""G3.3 review and approval over HTTP on real disposable Mongo and Redis.

Creator tokens are signed locally with roles; the inherited setup alone provisions
Enki. Verifies durable idempotency, cross-session recovery, Seshat evidence and that
no Agent, grant, approval-of-activation or runtime effect occurs.
"""

import asyncio
import copy
import os
import time
from uuid import uuid4

import httpx
import jwt
import pytest

from src.domains.agentverse.agents.creation_review.models import (
    APPROVALS_COLLECTION,
    ApprovalRevoked,
)
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

BASE = f"/api/v2/agentverse/agents/{ENKI}/creation-draft"
local_app = genesis_tests.local_app


def draft():
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {
            "name": "Reviewed Candidate",
            "type": "chat",
            "role_name": "general",
            "personality_key": "empty_eva",
            "prompt": "A definition approved for later governed creation.",
        },
    }


def token(roles):
    claims = {
        "tid": TENANT,
        "sub": OWNER,
        "sid": "g33-" + uuid4().hex,
        "exp": int(time.time() + 600),
        "iss": "CAOS-local",
        "aud": "CAOS-api",
        "roles": roles,
    }
    return jwt.encode(claims, os.environ["LOCAL_JWT_SECRET"], algorithm="HS256")


async def creator(s, roles=("agentverse-user", "caos.admin")):
    bearer = token(list(roles))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app),
        base_url="http://g33.test",
        headers={"Authorization": "Bearer " + bearer},
    )
    assert (await client.post("/api/v2/agentverse/session/local")).status_code == 201
    return client


def approve(digest, key):
    return {
        "contract": "caos.agent_creation_review_approval",
        "schema_version": 1,
        "draft": draft(),
        "review_digest": digest,
        "decision": "approve",
        "confirmed": True,
        "idempotency_key": key,
    }


@pytest.mark.asyncio
async def test_review_approval_is_durable_idempotent_audited_and_effect_free(local_app):
    s = await with_enki(local_app)
    agents = await s.mongo.db["coll_agents"].find({}).to_list(length=None)
    grants = copy.deepcopy(s.av.synaptic_authorization()._grants)
    approvals_before = dict(s.av.genesis_approvals())
    instances = dict(s.av.instance_registry_v2()._map)
    client = await creator(s)
    try:
        review = (await client.post(f"{BASE}/validate", json=draft())).json()
        assert review["contract"] == "caos.agent_creation_review", review
        assert review["eligibility"] == {"eligible": True, "reasons": []}
        digest = review["review_digest"]
        key = "g33-" + uuid4().hex
        responses = await asyncio.gather(
            *[client.post(f"{BASE}/approvals", json=approve(digest, key)) for _ in range(4)]
        )
        assert {r.status_code for r in responses} == {200}, [r.text for r in responses]
        bodies = [r.json() for r in responses]
        assert len({b["approval_id"] for b in bodies}) == 1
        assert {b["state"] for b in bodies} == {"approved"}
        assert all(b["authorizes"] == {"creation": False, "activation": False} for b in bodies)
    finally:
        await client.aclose()

    # Re-authentication: a new session of the same human replays the same decision.
    client = await creator(s)
    try:
        replay = await client.post(f"{BASE}/approvals", json=approve(digest, key))
        assert replay.json() == bodies[0]
        other = approve(digest, key)
        other["draft"]["creation"]["prompt"] = "Different content under a reused key."
        assert (await client.post(f"{BASE}/approvals", json=other)).status_code == 409
        stored = await s.mongo.db[APPROVALS_COLLECTION].find({}).to_list(length=None)
        assert len(stored) == 1 and stored[0]["status"] == "approved"
        evidence = (
            await s.mongo.db["coll_logs"]
            .find({"event_type": "agent.creation_review.approved"})
            .to_list(length=None)
        )
        assert len(evidence) == 1
        assert evidence[0]["metadata"]["review_digest"] == digest
        assert evidence[0]["actor"]["user_id"] == OWNER
        approvals = s.app.state.agent_draft_review_approvals
        effective = await approvals.effective(TENANT, OWNER, bodies[0]["approval_id"])
        assert effective.approval_event_id == evidence[0]["event_id"]
        revoked = await client.post(f"{BASE}/approvals/{bodies[0]['approval_id']}/revoke")
        assert revoked.json()["state"] == "revoked"
        # Revocation is attributed to this later session, not the deciding one.
        (withdrawal,) = (
            await s.mongo.db["coll_logs"]
            .find({"event_type": "agent.creation_review.revoked"})
            .to_list(length=None)
        )
        assert withdrawal["actor"] == evidence[0]["actor"]
        assert (
            withdrawal["authorization"]["evidence_digest"]
            != evidence[0]["authorization"]["evidence_digest"]
        )
        with pytest.raises(ApprovalRevoked):
            await approvals.effective(TENANT, OWNER, bodies[0]["approval_id"])
        assert (
            await client.post(f"{BASE}/approvals", json=approve(digest, key))
        ).status_code == 409
    finally:
        await client.aclose()

    # No Agent, grant, approval of activation or runtime instance was created.
    assert await s.mongo.db["coll_agents"].find({}).to_list(length=None) == agents
    assert s.av.synaptic_authorization()._grants == grants
    assert dict(s.av.genesis_approvals()) == approvals_before
    assert dict(s.av.instance_registry_v2()._map) == instances


@pytest.mark.asyncio
async def test_non_creators_review_but_cannot_approve(local_app):
    s = await with_enki(local_app)
    client = await creator(s, roles=("agentverse-user", "caos.user"))
    try:
        review = (await client.post(f"{BASE}/validate", json=draft())).json()
        assert review["eligibility"] == {"eligible": False, "reasons": ["requester.cannot_create"]}
        assert review["review_digest"] is None
        denied = await client.post(
            f"{BASE}/approvals", json=approve("a" * 64, "g33-" + uuid4().hex)
        )
        assert denied.status_code == 403
        assert await s.mongo.db[APPROVALS_COLLECTION].count_documents({}) == 0
    finally:
        await client.aclose()
