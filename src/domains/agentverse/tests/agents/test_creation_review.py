"""G3.3 review envelope, human approval, durable idempotency and Seshat evidence.

Real UTU/CPBX/validator/composer from the gateway stack; storage is an in-memory
MongoDB-like collection (real Mongo coverage: test_creation_review_integration.py).
"""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import AutoReconnect

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_review import models
from src.domains.agentverse.agents.creation_review.models import (
    ApprovalExpired,
    ApprovalNotEffective,
    ApprovalNotFound,
    ApprovalRevoked,
    ApprovalUnavailable,
    CorruptApproval,
    IdempotencyConflict,
    ReviewApprovalRequest,
    ReviewNotEligible,
    ReviewStale,
)
from src.domains.agentverse.agents.creation_review.repository import (
    MongoDraftReviewApprovalRepository,
)
from src.domains.agentverse.agents.creation_review.service import (
    APPROVED,
    REVOKED,
    DraftReviewApprovalService,
)
from src.domains.agentverse.audit.repository import LogUnavailable
from src.domains.agentverse.interfaces.api.v2 import genesis_agents
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.security.validation_gateway import PREVIEW_FIELDS, REVIEW_TTL
from src.domains.agentverse.tests.agents.test_validation_gateway import (  # noqa: F401
    request,
    stack,
)
from src.domains.agentverse.tests.audit.test_caos_log import FakeCollection, FakeMongo
from src.domains.agentverse.tests.audit.test_caos_log import service as seshat
from src.domains.agentverse.tools.validation_tool.models import validation_digest

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
CREATOR = User(id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["agentverse-user", "team.admin"])


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class ApprovalCollection(FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.update_error: Exception | None = None

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any:
        self.calls.append("update_one")
        if self.update_error is not None:
            error, self.update_error = self.update_error, None
            raise error
        row = self.documents.get(query["_id"])
        matched = row is not None and all(
            row.get(k) in v["$in"] if isinstance(v, dict) else row.get(k) == v
            for k, v in query.items()
            if k != "_id"
        )
        if matched:
            row.update(copy.deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=int(matched))


class ApprovalMongo(FakeMongo):
    def __init__(self) -> None:
        self.collection = ApprovalCollection()
        self.names = []


async def allow(_requester: User) -> None:
    return None


@pytest.fixture
def review(stack):  # noqa: F811
    stack.env.sessions.resource_requester.return_value = CREATOR
    stack.gateway._clock = stack.clock = Clock()
    stack.store = ApprovalMongo()
    stack.audit = seshat()
    stack.approvals = DraftReviewApprovalService(
        gateway=stack.gateway,
        repository=MongoDraftReviewApprovalRepository(stack.store),
        audit=stack.audit.service,
        clock=stack.clock,
    )
    return stack


def approval(digest: str, *, key: str = "approval-key-0001", draft=None) -> ReviewApprovalRequest:
    return ReviewApprovalRequest(
        contract="caos.agent_creation_review_approval",
        schema_version=1,
        draft=draft or request(),
        review_digest=digest,
        decision="approve",
        confirmed=True,
        idempotency_key=key,
    )


async def reviewed(s, draft=None):
    return await s.gateway.review(s.env.session, "source", draft or request())


def records(s):
    return list(s.audit.mongo.collection.documents.values())


# --- A. Authoritative review envelope -----------------------------------------------------


@pytest.mark.asyncio
async def test_envelope_is_authoritative_versioned_and_keeps_internals_private(review):
    s = review
    result = await reviewed(s)
    envelope = result.envelope
    assert (envelope["contract"], envelope["schema_version"]) == ("caos.agent_creation_review", 1)
    assert envelope["eligibility"] == {"eligible": True, "reasons": []}
    assert envelope["review_digest"] == result.digest and len(result.digest) == 64
    assert envelope["validated_at"] == "2026-10-30T09:00:00Z"
    assert envelope["expires_at"] == (T0 + REVIEW_TTL).isoformat().replace("+00:00", "Z")
    assert envelope["authorizes"] == {"creation": False, "activation": False}
    assert tuple(envelope["composition"]["preview"]) == PREVIEW_FIELDS
    assert envelope["validation"]["resolved"] is None
    # The full candidate (with server backend selectors) is bound but never public.
    assert {"llm_type", "dna_sequence"} <= result.snapshot["candidate"].keys()
    assert not {"llm_type", "dna_sequence", "chat_url"} & envelope["composition"]["preview"].keys()
    assert "snapshot" not in envelope and "fingerprints" not in str(envelope)
    assert set(result.snapshot["resolved"]["fingerprints"]) >= {"type", "role", "personality"}
    # The historical normalized digest is unchanged by the review.
    assert result.snapshot["normalized_digest"] == validation_digest(
        "agent", result.snapshot["normalized"], tenant_id=KEY.tenant_id, subject_id=KEY.user_id
    )


@pytest.mark.asyncio
async def test_digest_is_content_bound_and_time_independent(review):
    s = review
    first = await reviewed(s)
    s.clock.now = T0 + timedelta(minutes=30)
    again = await reviewed(s)
    assert again.digest == first.digest and again.validated_at != first.validated_at
    assert (
        await reviewed(s, request(prompt="A different reviewed prompt."))
    ).digest != first.digest
    s.env.sessions.resource_requester.return_value = CREATOR.model_copy(
        update={"roles": [*CREATOR.roles, "other.user"]}
    )
    assert (await reviewed(s)).digest != first.digest
    s.env.sessions.resource_requester.return_value = CREATOR
    s.catalogs["role"].get_info("general").metadata["revision"] = "changed"
    assert (await reviewed(s)).digest != first.digest
    del s.catalogs["role"].get_info("general").metadata["revision"]
    assert (await reviewed(s)).digest == first.digest
    versions = s.env.revocations.read.return_value
    s.env.revocations.read.return_value = (versions[0], "incarnation-2", *versions[2:])
    # Revocation counters are not policy: only the policy revision is bound.
    assert (await reviewed(s)).digest == first.digest


@pytest.mark.asyncio
async def test_ineligible_reviews_have_no_digest_or_snapshot(review):
    s = review
    s.env.sessions.resource_requester.return_value = User(
        id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["team.user"]
    )
    result = await reviewed(s)
    assert result.envelope["eligibility"] == {
        "eligible": False,
        "reasons": ["requester.cannot_create"],
    }
    assert (result.digest, result.snapshot, result.envelope["review_digest"]) == (None,) * 3
    s.env.sessions.resource_requester.return_value = CREATOR
    invalid = await reviewed(s, request(personality_key="missing"))
    assert not invalid.eligible and invalid.envelope["eligibility"]["reasons"]


# --- B/C. Approval, idempotency, recovery, concurrency, expiry, revocation ----------------


@pytest.mark.asyncio
async def test_approval_requires_exact_digest_and_records_evidence_before_effect(review):
    s = review
    resolved = []
    resolve = s.env.registry.resolve

    def spy(signal, version=None):
        resolved.append(signal)
        return resolve(signal, version)

    s.env.registry.resolve = spy
    digest = (await reviewed(s)).digest
    for stale in ("0" * 64,):
        with pytest.raises(ReviewStale):
            await s.approvals.approve(
                s.env.session, "source", approval(stale), authorize_creator=allow
            )
    assert s.store.collection.documents == {} and records(s) == []
    result = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    assert result.status == "approved" and result.review_digest == digest
    (record,) = records(s)
    assert record["event_type"] == APPROVED and record["event_id"] == result.approval_event_id
    assert (record["actor"]["principal_id"], record["actor"]["user_id"]) == (
        KEY.agent_id,
        KEY.user_id,
    )
    assert record["metadata"]["review_digest"] == digest
    public = result.public(s.clock.now)
    assert public["state"] == "approved" and public["authorizes"] == {
        "creation": False,
        "activation": False,
    }
    assert not {"snapshot", "authorization", "subject_id", "executor_id"} & public.keys()
    # Only data.validation executed; Store, grants and activation were unreachable.
    assert set(resolved) == {"data.validation"}


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_key_reuse_for_another_request_conflicts(review):
    s = review
    digest = (await reviewed(s)).digest
    first = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    s.clock.now = T0 + timedelta(minutes=5)
    again = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    assert again == first and len(records(s)) == 1
    other = request(prompt="Another reviewed prompt.")
    other_digest = (await reviewed(s, other)).digest
    with pytest.raises(IdempotencyConflict):
        await s.approvals.approve(
            s.env.session, "source", approval(other_digest, draft=other), authorize_creator=allow
        )
    second = await s.approvals.approve(
        s.env.session,
        "source",
        approval(other_digest, key="approval-key-0002", draft=other),
        authorize_creator=allow,
    )
    assert second.approval_id != first.approval_id


@pytest.mark.asyncio
async def test_denied_creator_and_ineligible_review_persist_nothing(review):
    s = review

    async def deny(_requester: User) -> None:
        raise EventPermissionError("not a creator")

    digest = (await reviewed(s)).digest
    with pytest.raises(EventPermissionError):
        await s.approvals.approve(s.env.session, "source", approval(digest), authorize_creator=deny)
    s.env.sessions.resource_requester.return_value = User(
        id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["team.user"]
    )
    with pytest.raises(ReviewNotEligible):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    assert s.store.collection.documents == {} and records(s) == []


@pytest.mark.asyncio
async def test_partial_failures_stay_pending_and_recover_with_one_evidence_record(review):
    s = review
    digest = (await reviewed(s)).digest
    s.audit.mongo.collection.insert_error = LogUnavailable("down")
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    (stored,) = s.store.collection.documents.values()
    assert stored["status"] == "pending"
    s.audit.mongo.collection.insert_error = None
    # Evidence committed, then the state transition is lost.
    s.store.collection.update_error = AutoReconnect("lost")
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    assert stored["status"] == "pending" and len(records(s)) == 1
    # Repeating the request resumes with identical evidence (cross-session: integration).
    result = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    assert result.status == "approved" and len(records(s)) == 1


@pytest.mark.asyncio
async def test_authority_lost_before_effect_leaves_the_approval_pending(review):
    s = review
    digest = (await reviewed(s)).digest
    original = s.audit.service.append

    async def revoke_during_audit(event, *, authorization):
        result = await original(event, authorization=authorization)
        s.env.revocations.read.return_value = ("superseded", "incarnation", "0", "0")
        return result

    s.audit.service.append = revoke_during_audit
    with pytest.raises(EventPermissionError):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    (stored,) = s.store.collection.documents.values()
    assert stored["status"] == "pending" and stored["approved_at"] is None


@pytest.mark.asyncio
async def test_concurrent_same_key_approvals_converge_on_one_effective_record(review):
    s = review
    digest = (await reviewed(s)).digest
    results = await asyncio.gather(
        *[
            s.approvals.approve(s.env.session, "source", approval(digest), authorize_creator=allow)
            for _ in range(5)
        ]
    )
    assert len({r.approval_id for r in results}) == 1
    assert {r.status for r in results} == {"approved"}
    assert len({r.approved_at for r in results}) == 1
    assert len(s.store.collection.documents) == 1 and len(records(s)) == 1


@pytest.mark.asyncio
async def test_expiry_and_revocation_semantics(review):
    s = review
    digest = (await reviewed(s)).digest
    s.audit.mongo.collection.insert_error = LogUnavailable("down")
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    s.audit.mongo.collection.insert_error = None
    s.clock.now = T0 + models.APPROVAL_TTL
    # A pending approval can never take effect after its expiry.
    with pytest.raises(ApprovalExpired):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    s.clock.now = T0
    approved = await s.approvals.approve(
        s.env.session, "source", approval(digest, key="approval-key-0002"), authorize_creator=allow
    )
    assert approved.public(T0 + models.APPROVAL_TTL)["state"] == "expired"
    for other in (replace(s.env.session, user_id="intruder"),):
        with pytest.raises(ApprovalNotFound):
            await s.approvals.revoke(other, "source", approved.approval_id)
    with pytest.raises(ApprovalNotFound):
        await s.approvals.revoke(s.env.session, "other-source", approved.approval_id)
    # Revocation takes effect even when its evidence cannot be recorded yet.
    s.audit.mongo.collection.insert_error = LogUnavailable("down")
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.revoke(s.env.session, "source", approved.approval_id)
    stored = await s.approvals._repository.get(KEY.tenant_id, approved.approval_id)
    assert stored.state(T0) == "revoked" and stored.revocation_event_id is None
    s.audit.mongo.collection.insert_error = None
    revoked = await s.approvals.revoke(s.env.session, "source", approved.approval_id)
    assert revoked.revocation_event_id and revoked.public(T0)["state"] == "revoked"
    assert [r["event_type"] for r in records(s)].count(REVOKED) == 1
    with pytest.raises(ApprovalRevoked):
        await s.approvals.approve(
            s.env.session,
            "source",
            approval(digest, key="approval-key-0002"),
            authorize_creator=allow,
        )


@pytest.mark.asyncio
async def test_tampered_snapshot_is_detected_on_load(review):
    s = review
    digest = (await reviewed(s)).digest
    result = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    stored = s.store.collection.documents[result.approval_id]
    stored["snapshot_json"] = stored["snapshot_json"].replace("general", "writer")
    with pytest.raises(CorruptApproval):
        await s.approvals._repository.get(KEY.tenant_id, result.approval_id)


# --- B. HTTP route: existing agent.write + can_create policy ------------------------------


@pytest.mark.asyncio
async def test_http_approval_enforces_existing_creator_policy(review, monkeypatch):
    s = review
    digest = (await reviewed(s)).digest

    async def authenticated(_request):
        yield s.env.session

    from contextlib import asynccontextmanager

    monkeypatch.setattr(genesis_agents, "authenticated_genesis", asynccontextmanager(authenticated))
    permissions: dict[str, Any] = {"value": None}

    class Authz:
        async def get_permissions(self, token):
            assert token == "bearer-token"
            return permissions["value"]

    app = FastAPI()
    app.container = SimpleNamespace(authz=SimpleNamespace(authz_client=lambda: None))
    app.state.agent_draft_review_approvals = s.approvals
    app.include_router(genesis_agents.router)
    path = "/api/v2/agentverse/agents/source/creation-draft/approvals"
    body = approval(digest).model_dump(mode="json")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer bearer-token"},
    ) as client:
        for change in ({"confirmed": False}, {"decision": "create"}, {"grants": ["x"]}):
            assert (await client.post(path, json={**body, **change})).status_code == 422
        stale = {**body, "review_digest": "0" * 64}
        assert (await client.post(path, json=stale)).json()["detail"]["code"] == "review.stale"
        s.env.sessions.resource_requester.return_value = CREATOR.model_copy(
            update={"roles": ["team.admin"]}  # Not an agentverse-user.
        )
        assert (await client.post(path, json=body)).status_code == 403
        s.env.sessions.resource_requester.return_value = CREATOR
        app.container.authz.authz_client = lambda: Authz()
        permissions["value"] = {"agent.read"}
        assert (await client.post(path, json=body)).status_code == 403
        permissions["value"] = {"agent.read", "agent.write"}
        response = await client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "approved"
        revoke = f"{path}/{response.json()['approval_id']}/revoke"
        assert (await client.post(revoke)).json()["state"] == "revoked"
        assert (await client.post(path, json=body)).status_code == 409


# --- G3.3 closure: expiry boundary, attribution, effective-approval evidence -------------


async def approved(s, key="approval-key-0001"):
    digest = (await reviewed(s)).digest
    return await s.approvals.approve(
        s.env.session, "source", approval(digest, key=key), authorize_creator=allow
    )


def evidence(s, event_type):
    return [r for r in records(s) if r["event_type"] == event_type]


@pytest.mark.asyncio
async def test_an_approval_never_takes_effect_at_or_after_expiry(review):
    s = review
    digest = (await reviewed(s)).digest
    original = s.audit.service.append

    async def slow_audit(event, *, authorization):
        result = await original(event, authorization=authorization)
        # Audit and authority checks finish exactly at the deadline.
        s.clock.now = T0 + models.APPROVAL_TTL
        return result

    s.audit.service.append = slow_audit
    with pytest.raises(ApprovalExpired):
        await s.approvals.approve(
            s.env.session, "source", approval(digest), authorize_creator=allow
        )
    (stored,) = s.store.collection.documents.values()
    assert (stored["status"], stored["approved_at"]) == ("pending", None)
    # Evidence was written, yet the approval is not effective.
    assert len(evidence(s, APPROVED)) == 1
    with pytest.raises(ApprovalExpired):
        await s.approvals.effective(KEY.tenant_id, KEY.user_id, stored["approval_id"])
    record = await s.approvals._repository.get(KEY.tenant_id, stored["approval_id"])
    for when in (record.expires_at, record.expires_at + timedelta(seconds=1)):
        with pytest.raises(ValueError):
            models.DraftReviewApproval.model_validate(
                {
                    **record.model_dump(),
                    "status": "approved",
                    "approved_at": when,
                    "approval_event_id": "event",
                }
            )


@pytest.mark.asyncio
async def test_approval_and_revocation_are_attributed_to_their_own_authority(review):
    s = review
    result = await approved(s)
    (decision,) = evidence(s, APPROVED)
    # Actor: the executor that performed the reviewed validation, for this human.
    assert (decision["actor"]["principal_id"], decision["actor"]["user_id"]) == (
        KEY.agent_id,
        KEY.user_id,
    )
    assert decision["authorization"] == result.authorization.model_dump(mode="json")
    assert decision["authorization"].get("origin", "session") == "session"
    assert decision["tenant_id"] == KEY.tenant_id
    # Revocation happens later, under different (re-issued) authority.
    versions = s.env.revocations.read.return_value
    s.env.revocations.read.return_value = (versions[0], "incarnation-2", *versions[2:])
    revoked = await s.approvals.revoke(s.env.session, "source", result.approval_id)
    (withdrawal,) = evidence(s, REVOKED)
    assert revoked.revocation_authorization is not None
    assert revoked.revocation_authorization != result.authorization
    assert withdrawal["authorization"] == revoked.revocation_authorization.model_dump(mode="json")
    assert (
        withdrawal["authorization"]["evidence_digest"]
        != (decision["authorization"]["evidence_digest"])
    )
    # The decision record keeps its original attribution.
    assert revoked.authorization == result.authorization


@pytest.mark.asyncio
async def test_revocation_without_current_authority_is_effective_but_unattributed(review):
    s = review
    result = await approved(s)
    session = s.env.sessions.get_session.return_value
    s.env.sessions.get_session.return_value = None
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.revoke(s.env.session, "source", result.approval_id)
    stored = await s.approvals._repository.get(KEY.tenant_id, result.approval_id)
    assert stored.status == "revoked" and stored.revocation_authorization is None
    assert evidence(s, REVOKED) == []
    with pytest.raises(ApprovalRevoked):
        await s.approvals.effective(KEY.tenant_id, KEY.user_id, result.approval_id)
    s.env.sessions.get_session.return_value = session
    revoked = await s.approvals.revoke(s.env.session, "source", result.approval_id)
    assert revoked.revocation_event_id and len(evidence(s, REVOKED)) == 1
    # Repeating is a no-op: provenance and evidence are recorded once.
    assert await s.approvals.revoke(s.env.session, "source", result.approval_id) == revoked
    assert len(evidence(s, REVOKED)) == 1


@pytest.mark.asyncio
async def test_effective_approval_requires_the_record_and_its_exact_evidence(review):
    s = review
    result = await approved(s)
    assert await s.approvals.effective(KEY.tenant_id, KEY.user_id, result.approval_id) == result
    with pytest.raises(ApprovalNotFound):
        await s.approvals.effective(KEY.tenant_id, "other-user", result.approval_id)
    with pytest.raises(ApprovalNotFound):
        await s.approvals.effective("other-tenant", KEY.user_id, result.approval_id)
    # Evidence alone is not effect: committed evidence, lost state transition.
    digest = (await reviewed(s)).digest
    s.store.collection.update_error = AutoReconnect("lost")
    with pytest.raises(ApprovalUnavailable):
        await s.approvals.approve(
            s.env.session,
            "source",
            approval(digest, key="approval-key-0002"),
            authorize_creator=allow,
        )
    pending = next(d for d in s.store.collection.documents.values() if d["status"] == "pending")
    assert len(evidence(s, APPROVED)) == 2
    with pytest.raises(ApprovalNotEffective):
        await s.approvals.effective(KEY.tenant_id, KEY.user_id, pending["approval_id"])
    # An approved record without its evidence is not effective (fails closed).
    (stored_evidence,) = [
        key
        for key, doc in s.audit.mongo.collection.documents.items()
        if doc["event_id"] == result.approval_event_id
    ]
    removed = s.audit.mongo.collection.documents.pop(stored_evidence)
    with pytest.raises(ApprovalNotEffective):
        await s.approvals.effective(KEY.tenant_id, KEY.user_id, result.approval_id)
    s.audit.mongo.collection.documents[stored_evidence] = removed
    s.clock.now = T0 + models.APPROVAL_TTL
    with pytest.raises(ApprovalExpired):
        await s.approvals.effective(KEY.tenant_id, KEY.user_id, result.approval_id)
