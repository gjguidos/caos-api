"""G3.4 governed manual creation over HTTP on real disposable Mongo and Redis.

Effective approval → creation authorization → Genesis → Store → sleeping Agent, with
durable idempotency, crash/audit recovery and the complete Seshat chain. Creator
tokens are signed locally with roles; the inherited setup alone provisions Enki.
"""

import asyncio
import copy
from uuid import uuid4

import pytest

from src.domains.agentverse.agents.creation_review.creation import (
    AUTHORIZED,
    COMPLETED,
    CREATIONS_COLLECTION,
)
from src.domains.agentverse.audit.repository import LogUnavailable
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_creation_review_integration import (
    approve,
    creator,
    draft,
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

BASE = f"/api/v2/agentverse/agents/{ENKI}/creation-draft"
GENESIS = "00000000-0000-4000-8000-000000000003"
local_app = genesis_tests.local_app


class Crash(Exception):
    """The worker dies after Store committed, before Genesis records it."""


async def approved(s, client, body=None):
    body = body or draft()
    review = (await client.post(f"{BASE}/validate", json=body)).json()
    assert review["eligibility"]["eligible"], review
    approval = approve(review["review_digest"], "g34-" + uuid4().hex)
    approval["draft"] = body
    response = await client.post(f"{BASE}/approvals", json=approval)
    assert response.status_code == 200, response.text
    return response.json()


def create(approval):
    return {
        "contract": "caos.agent_creation_request",
        "schema_version": 1,
        "approval_id": approval["approval_id"],
        "expected_review_digest": approval["review_digest"],
    }


def key():
    return {"Idempotency-Key": "g34-" + uuid4().hex}


async def snapshot(s):
    return {
        "grants": copy.deepcopy(s.av.synaptic_authorization()._grants),
        "approvals": dict(s.av.genesis_approvals()),
        "instances": dict(s.av.instance_registry_v2()._map),
        "calls": len(s.model.calls),
    }


async def unchanged(s, before):
    after = await snapshot(s)
    assert after == before, "creation must not grant, approve, activate or infer"


@pytest.mark.asyncio
async def test_effective_approval_creates_one_sleeping_agent_with_complete_evidence(local_app):
    s = await with_enki(local_app)
    client = await creator(s)
    try:
        approval = await approved(s, client)
        before = await snapshot(s)
        agents_before = await s.mongo.db["coll_agents"].count_documents({})
        headers = key()
        response = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "completed" and result["authorizes"] == {"activation": False}
        agent_id = result["agent_id"]

        # One Agent, persisted by Store, with the reviewed composition, asleep.
        assert await s.mongo.db["coll_agents"].count_documents({}) == agents_before + 1
        agent = await s.mongo.db["coll_agents"].find_one({"id": agent_id})
        assert (agent["type"], agent["role_name"], agent["personality_key"]) == (
            "chat",
            "general",
            "empty_eva",
        )
        assert (agent["runtime_version"], agent["tenant_id"], agent["creator"]) == (
            "v2",
            TENANT,
            OWNER,
        )
        assert [c["type"] for c in agent["gui_components"]] == ["chat"]
        assert agent["store"]["authorization"]["principal_id"] == GENESIS
        await unchanged(s, before)

        # Replay: identical outcome, no second Agent or evidence.
        logs = await s.mongo.db["coll_logs"].count_documents({})
        again = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert again.json() == result
        assert await s.mongo.db["coll_logs"].count_documents({}) == logs
        assert await s.mongo.db["coll_agents"].count_documents({}) == agents_before + 1

        # Evidence chain: approval → authorization → Genesis → Store → completion.
        operation = result["operation_id"]
        chain = await s.mongo.db["coll_logs"].find({"correlation_id": operation}).to_list(None)
        types = [r["event_type"] for r in chain]
        for expected in (
            AUTHORIZED,
            "genesis.validation.completed",
            "genesis.composition.completed",
            "genesis.store.completed",
            COMPLETED,
        ):
            assert types.count(expected) == 1, types
        by_type = {r["event_type"]: r for r in chain}
        assert by_type[AUTHORIZED]["causation_id"] == approval["audit_event_id"]
        assert by_type[COMPLETED]["causation_id"] == by_type["genesis.store.completed"]["event_id"]
        for record in chain:
            assert record["actor"] == {"principal_id": GENESIS, "user_id": OWNER}
            assert record["tenant_id"] == TENANT
        assert by_type[COMPLETED]["metadata"]["target_agent_id"] == agent_id
        assert by_type[COMPLETED]["metadata"]["approval_id"] == approval["approval_id"]
        assert "candidate" not in str(by_type[COMPLETED]["metadata"])

        # The approval is consumed: another creation request is refused.
        other = await client.post(f"{BASE}/create", json=create(approval), headers=key())
        assert other.status_code == 409 and other.json()["detail"]["code"] == "approval.consumed"
        # Same key, different request.
        second = await approved(
            s, client, {**draft(), "creation": {**draft()["creation"], "name": "Second Candidate"}}
        )
        conflict = await client.post(f"{BASE}/create", json=create(second), headers=headers)
        assert conflict.json()["detail"]["code"] == "idempotency.conflict"

        # A later revocation never deletes the provisioned Agent.
        await client.post(f"{BASE}/approvals/{approval['approval_id']}/revoke")
        assert await s.mongo.db["coll_agents"].find_one({"id": agent_id}) is not None
        assert (
            await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        ).json() == result
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_requests_and_crash_after_store_converge_on_one_agent(local_app):
    s = await with_enki(local_app)
    client = await creator(s)
    service = s.app.state.agent_creation
    service._provisioner._lease_ms = 50
    try:
        approval = await approved(s, client)
        headers = key()
        binding = s.registry.resolve("store.create.approved", "1.0.0")
        handler = binding.handler
        crashed = []

        async def crash_once(synapse, store_client):
            result = await handler(synapse, store_client)
            if not crashed:
                crashed.append(result)
                raise Crash()
            return result

        object.__setattr__(binding, "handler", crash_once)
        with pytest.raises(Crash):
            await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        record = await s.mongo.db[CREATIONS_COLLECTION].find_one({})
        assert record["state"] == "provisioning"
        # Store committed; nothing is reported as completed or failed yet.
        target = record["target_agent_id"]
        assert await s.mongo.db["coll_agents"].find_one({"id": target}) is not None
        responses = await asyncio.gather(
            *[
                client.post(f"{BASE}/create", json=create(approval), headers=headers)
                for _ in range(4)
            ]
        )
        assert {r.status_code for r in responses} <= {200, 202}
        await asyncio.sleep(0.1)
        final = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert final.status_code == 200 and final.json()["agent_id"] == target
        assert await s.mongo.db["coll_agents"].count_documents({"id": target}) == 1
        assert await s.mongo.db[CREATIONS_COLLECTION].count_documents({}) == 1
        completed = await s.mongo.db["coll_logs"].count_documents({"event_type": COMPLETED})
        assert completed == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_completion_audit_failure_is_recoverable_and_never_final(local_app):
    s = await with_enki(local_app)
    client = await creator(s)
    service = s.app.state.agent_creation
    append = service._audit.append
    failed = []

    async def flaky(event, *, authorization):
        if event.event_type == COMPLETED and not failed:
            failed.append(event)
            raise LogUnavailable("down")
        return await append(event, authorization=authorization)

    service._audit.append = flaky
    try:
        approval = await approved(s, client)
        headers = key()
        response = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert response.status_code == 503 and response.json()["detail"]["retryable"] is True
        record = await s.mongo.db[CREATIONS_COLLECTION].find_one({})
        assert record["state"] == "provisioning" and record["agent_id"] is None
        retry = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert retry.status_code == 200 and retry.json()["state"] == "completed"
        assert retry.json()["agent_id"] == record["target_agent_id"]
    finally:
        service._audit.append = append
        await client.aclose()


@pytest.mark.asyncio
async def test_revoked_stale_foreign_and_non_creator_requests_create_nothing(local_app):
    s = await with_enki(local_app)
    client = await creator(s)
    agents = await s.mongo.db["coll_agents"].count_documents({})
    try:
        revoked = await approved(s, client)
        await client.post(f"{BASE}/approvals/{revoked['approval_id']}/revoke")
        response = await client.post(f"{BASE}/create", json=create(revoked), headers=key())
        assert response.json()["detail"]["code"] == "approval.revoked"

        stale = await approved(s, client)
        mismatch = {**create(stale), "expected_review_digest": "0" * 64}
        response = await client.post(f"{BASE}/create", json=mismatch, headers=key())
        assert response.json()["detail"]["code"] == "review.stale"
        # A dependency changed after approval: re-review no longer reproduces the digest.
        from src.domains.agentverse import registries as registry_instances

        role = registry_instances.role_registry_instance.get_info("general")
        role.metadata["revision"] = "changed-after-approval"
        try:
            response = await client.post(f"{BASE}/create", json=create(stale), headers=key())
            assert response.json()["detail"]["code"] == "review.stale"
        finally:
            del role.metadata["revision"]

        foreign = await approved(s, client)
        wrong = await client.post(
            f"/api/v2/agentverse/agents/{GENESIS}/creation-draft/create",
            json=create(foreign),
            headers=key(),
        )
        assert wrong.status_code == 403
        for body in ({**create(foreign), "executor_id": GENESIS}, {**create(foreign), "agent": {}}):
            assert (
                await client.post(f"{BASE}/create", json=body, headers=key())
            ).status_code == 422
        assert (await client.post(f"{BASE}/create", json=create(foreign))).status_code == 422
        assert (
            await client.post(
                f"{BASE}/create", json=create(foreign), headers={"Idempotency-Key": "x"}
            )
        ).status_code == 422
    finally:
        await client.aclose()
    viewer = await creator(s, roles=("agentverse-user", "caos.user"))
    try:
        denied = await viewer.post(f"{BASE}/create", json=create(foreign), headers=key())
        assert denied.status_code == 403
    finally:
        await viewer.aclose()
    assert await s.mongo.db["coll_agents"].count_documents({}) == agents


# The exact reviewed first live configuration (docs/GENESIS_MANUAL_CREATION.md §8).
NABU_DRAFT = {
    "contract": "caos.agent_creation_draft",
    "schema_version": 1,
    "resource_type": "agent",
    "creation": {
        "name": "Nabu",
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "visibility": "private",
        "prompt": "You are Nabu, a concise assistant for verifying governed Agent creation in CAOS.",
    },
}


@pytest.mark.asyncio
async def test_nabu_acceptance_scenario(local_app):
    """Named acceptance rehearsal of the first live manual creation (disposable only)."""
    s = await with_enki(local_app)
    client = await creator(s)
    service = s.app.state.agent_creation
    service._provisioner._lease_ms = 50
    try:
        before = await snapshot(s)
        # G3.3: authoritative validation and explicit human approval of the exact draft.
        approval = await approved(s, client, NABU_DRAFT)
        assert approval["state"] == "approved"
        headers = key()

        # G3.4 with a crash after Store committed, then reconciliation by retry.
        binding = s.registry.resolve("store.create.approved", "1.0.0")
        handler = binding.handler
        crashed = []

        async def crash_once(synapse, store_client):
            # Scoped Store authority: only the creation scope's permit reaches Store.
            permit = store_client.execution_permit
            assert permit.scope.operation == "agent.creation_draft.create"
            result = await handler(synapse, store_client)
            if not crashed:
                crashed.append(result)
                raise Crash()
            return result

        object.__setattr__(binding, "handler", crash_once)
        with pytest.raises(Crash):
            await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        await asyncio.sleep(0.1)
        response = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        for _ in range(5):
            if response.status_code == 200:
                break
            await asyncio.sleep(0.1)
            response = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "completed"
        nabu_id = result["agent_id"]

        nabu = await s.mongo.db["coll_agents"].find_one({"id": nabu_id})
        assert (
            nabu["name"],
            nabu["type"],
            nabu["role_name"],
            nabu["personality_key"],
            nabu["visibility"],
            nabu["runtime_version"],
            nabu["creator"],
        ) == ("Nabu", "chat", "general", "empty_eva", "private", "v2", OWNER)
        assert nabu["store"]["authorization"]["principal_id"] == GENESIS
        assert await s.mongo.db["coll_agents"].count_documents({"name": "Nabu"}) == 1

        # Idempotent replay; the approval cannot create a second Nabu.
        again = await client.post(f"{BASE}/create", json=create(approval), headers=headers)
        assert again.json() == result
        other = await client.post(f"{BASE}/create", json=create(approval), headers=key())
        assert other.json()["detail"]["code"] == "approval.consumed"
        assert await s.mongo.db["coll_agents"].count_documents({"name": "Nabu"}) == 1

        # Evidence chain and sleeping state: nothing activated, granted or inferred.
        chain = (
            await s.mongo.db["coll_logs"]
            .find({"correlation_id": result["operation_id"]})
            .to_list(None)
        )
        assert {r["event_type"] for r in chain} >= {
            AUTHORIZED,
            "genesis.store.completed",
            COMPLETED,
        }
        assert not any("activation" in r["event_type"] for r in chain)
        await unchanged(s, before)
    finally:
        await client.aclose()
