"""REV-3: governed human approval of Agent revision reviews.

Real (disposable) MongoDB and Seshat; real UTU/CPBX scoped authority, ``data.validation``
and composer (the REV-2 fixture). An approval never applies a revision, writes an Agent
definition or grants anything.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.handlers.exception import register_exception_handlers
from src.domains.agentverse.agents.definition_revisions.approval import (
    APPROVALS_COLLECTION,
    APPROVED,
    WITHDRAWN,
    MongoRevisionApprovalRepository,
    RevisionApprovalDigestMismatch,
    RevisionApprovalNotFound,
    RevisionApprovalRequest,
    RevisionApprovalService,
    RevisionApprovalUnavailable,
    RevisionApprovalWithdrawn,
    RevisionWithdrawalRequest,
)
from src.domains.agentverse.agents.definition_revisions.gateway import RevisionGatewayBinding
from src.domains.agentverse.agents.definition_revisions.models import REVISIONS_COLLECTION
from src.domains.agentverse.agents.definition_revisions.review import RevisionReviewNotCurrent
from src.domains.agentverse.agents.definition_revisions.service import RevisionTargetNotFound
from src.domains.agentverse.agents.definition_revisions.tests.test_revision_review import (
    KEY_1,
    create,
    rev,  # noqa: F401 - fixture re-export
    snapshot,
    update,
)
from src.domains.agentverse.audit.models import LOGS_COLLECTION
from src.domains.agentverse.audit.repository import LogUnavailable, MongoLogRepository
from src.domains.agentverse.audit.service import CAOSLogService
from src.domains.agentverse.interfaces.api.v2 import agent_definition_revisions as routes
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY, STORE, VALIDATE
from src.domains.agentverse.security.revocation import policy_version

WITHDRAW = RevisionWithdrawalRequest(
    contract="caos.agent_definition_revision_withdrawal_request", schema_version=1, confirmed=True
)


@pytest_asyncio.fixture
async def appr(rev):  # noqa: F811
    r = rev
    audit = CAOSLogService(repository=MongoLogRepository(r.mongo))
    repository = MongoRevisionApprovalRepository(r.mongo)
    service = RevisionApprovalService(
        revisions=r.service, repository=repository, audit=audit, clock=r.clock
    )
    draft, _ = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    assert review.eligible
    return SimpleNamespace(
        r=r, audit=audit, repository=repository, service=service, draft=draft, review=review
    )


def approve_request(review, **changes):
    return RevisionApprovalRequest.model_validate(
        {
            "contract": "caos.agent_definition_revision_approval_request",
            "schema_version": 1,
            "review_id": review.review_id,
            "review_digest": review.review_digest,
            "decision": "approve",
            "confirmed": True,
            **changes,
        }
    )


async def events(a, event_type=None):
    logs = await a.r.mongo.get_collection(LOGS_COLLECTION)
    query = (
        {"event_type": event_type} if event_type else {"event_type": {"$in": [APPROVED, WITHDRAWN]}}
    )
    return await logs.find(query, {"_id": 0}).to_list(None)


# --- Approval -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_current_review_is_approved_with_bounded_evidence_and_no_application(appr):
    a, r = appr, appr.r
    before = await snapshot(r)
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert (record.status, record.review_digest) == ("approved", a.review.review_digest)
    assert record.predecessor == a.draft.predecessor
    assert record.authorization.origin == "session"
    (event,) = await events(a, APPROVED)
    assert event["subject"] == {"resource_type": "agent", "resource_id": r.agent_id}
    assert event["metadata"]["candidate_definition_digest"] == record.candidate_definition_digest
    text = json.dumps(event, default=str)
    # Never draft content, the snapshot, resolved dependencies or definitions.
    for private in ("You are Nabu.", 'candidate_definition"', "resolved", "normalized"):
        assert private not in text
    status = await a.service.status(r.session, r.agent_id, record.approval_id)
    assert (status["state"], status["effective"]) == ("approved", True)
    assert status["authorizes"] == {"application": False, "activation": False}
    assert not {"authorization", "utu_policy_revision", "gateway_fingerprint", "executor_id"} & set(
        status
    )
    assert (await a.service.effective(r.session, r.agent_id, record.approval_id)) == record
    # No application: Agent, revision history and Store are untouched.
    assert await snapshot(r) == before
    assert REVISIONS_COLLECTION not in await r.agents.database.list_collection_names()


@pytest.mark.asyncio
async def test_repeated_and_concurrent_approvals_converge_on_one_decision(appr):
    a, r = appr, appr.r
    outcomes = await asyncio.gather(
        *(a.service.approve(r.session, r.agent_id, approve_request(a.review)) for _ in range(8))
    )
    assert len({o.approval_id for o in outcomes}) == 1
    assert {o.status for o in outcomes} == {"approved"}
    again = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert again == outcomes[0]
    assert len(await events(a, APPROVED)) == 1
    approvals = await r.mongo.get_collection(APPROVALS_COLLECTION)
    assert await approvals.count_documents({}) == 1


@pytest.mark.asyncio
async def test_approval_and_withdrawal_races_end_withdrawn(appr):
    a, r = appr, appr.r
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    results = await asyncio.gather(
        a.service.approve(r.session, r.agent_id, approve_request(a.review)),
        a.service.withdraw(r.session, r.agent_id, record.approval_id, WITHDRAW),
        return_exceptions=True,
    )
    assert not [
        x
        for x in results
        if isinstance(x, Exception) and not isinstance(x, RevisionApprovalWithdrawn)
    ]
    final = await a.service.status(r.session, r.agent_id, record.approval_id)
    assert (final["state"], final["effective"]) == ("withdrawn", False)
    with pytest.raises(RevisionApprovalWithdrawn):
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert len(await events(a, WITHDRAWN)) == 1


@pytest.mark.asyncio
async def test_invalid_digests_and_ineligible_reviews_are_refused(appr):
    a, r = appr, appr.r
    with pytest.raises(RevisionApprovalDigestMismatch):
        await a.service.approve(
            r.session, r.agent_id, approve_request(a.review, review_digest="0" * 64)
        )
    draft, _ = await r.service.create_draft(
        r.session, r.agent_id, create(tools=[]), "draft-key-0000000009"
    )
    ineligible = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    with pytest.raises(RevisionReviewNotCurrent) as denied:
        await a.service.approve(
            r.session, r.agent_id, approve_request(ineligible, review_digest="1" * 64)
        )
    assert denied.value.code == "revision_review.ineligible"
    assert await events(a) == []


# --- Freshness ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_reviews_and_newer_drafts_cannot_be_approved(appr):
    a, r = appr, appr.r
    r.clock.now += timedelta(minutes=11)
    with pytest.raises(RevisionReviewNotCurrent) as expired:
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert expired.value.code == "revision_review.expired"
    r.clock.now -= timedelta(minutes=11)
    await r.service.update_draft(r.session, r.agent_id, a.draft.draft_id, update(1, prompt="New."))
    with pytest.raises(RevisionReviewNotCurrent) as stale:
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert stale.value.code == "revision_review.stale_candidate"


@pytest.mark.asyncio
async def test_a_changed_predecessor_blocks_approval_and_invalidates_an_approval(appr):
    a, r = appr, appr.r
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    # A post-creation write (e.g. pre-AGENT-DEF-SEC legacy edit) changes the predecessor.
    await r.agents.update_one(
        {"id": r.agent_id},
        {"$set": {"prompt": "Changed.", "updated_at": datetime(2026, 11, 1, tzinfo=UTC)}},
    )
    status = await a.service.status(r.session, r.agent_id, record.approval_id)
    assert (status["state"], status["effective"], status["reason"]) == (
        "approved",
        False,
        "revision_approval.stale_predecessor",
    )
    with pytest.raises(RevisionReviewNotCurrent) as stale:
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert stale.value.code == "revision_review.stale_predecessor"


@pytest.mark.asyncio
async def test_changed_authority_policy_or_gateway_fails_closed(appr):
    a, r = appr, appr.r
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    # A superseded permit (store version differs from the loaded policy) is denied.
    previous = r.env.revocations.read.return_value
    r.env.revocations.read.return_value = ("other-policy", "incarnation", "0", "0")
    with pytest.raises(EventPermissionError):
        await a.service.status(r.session, r.agent_id, record.approval_id)
    # A deployed new UTU policy revision: fresh permits work, but the review and the
    # approval were made under the previous revision, so neither is current.
    changed = SynapticAuthorization(
        grants={
            (KEY.tenant_id, KEY.agent_id): SynapticGrants(
                frozenset({VALIDATE.signal, STORE.signal, "unrelated.signal"})
            )
        }
    )
    r.env.authority.reload_policy(changed)
    r.env.revocations.read.return_value = (policy_version(changed), *previous[1:])
    status = await a.service.status(r.session, r.agent_id, record.approval_id)
    assert (status["effective"], status["reason"]) == (False, "revision_approval.stale_authority")
    with pytest.raises(RevisionReviewNotCurrent) as stale:
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert stale.value.code == "revision_review.stale_authority"
    # A removed or different gateway binding denies outright.
    r.service._gateway._bindings = {}
    with pytest.raises(EventPermissionError):
        await a.service.status(r.session, r.agent_id, record.approval_id)
    assert RevisionGatewayBinding(tenant_id="t", subject_id="s", executor_id="a").fingerprint() != (
        RevisionGatewayBinding(tenant_id="t", subject_id="s", executor_id="b").fingerprint()
    )


@pytest.mark.asyncio
async def test_revocation_during_a_decision_leaves_it_non_effective(appr):
    a, r = appr, appr.r
    append = a.audit.append

    async def revoke_after_evidence(event, *, authorization):
        result = await append(event, authorization=authorization)
        r.env.revocations.read.return_value = ("revoked", "incarnation", "1", "0")
        return result

    a.audit.append = revoke_after_evidence
    with pytest.raises(EventPermissionError):
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    approvals = await r.mongo.get_collection(APPROVALS_COLLECTION)
    (stored,) = await approvals.find({}, {"_id": 0}).to_list(None)
    assert stored["status"] == "pending" and stored["approval_event_id"] is None


# --- Identity -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_owners_tenants_bootstrap_and_agent_paths_are_denied(appr):
    a, r = appr, appr.r
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    for session in (
        dataclasses.replace(r.session, user_id="intruder"),
        dataclasses.replace(r.session, origin="bootstrap"),
        dataclasses.replace(r.session, active=False),
    ):
        with pytest.raises(EventPermissionError):
            await a.service.status(session, r.agent_id, record.approval_id)
        with pytest.raises(EventPermissionError):
            await a.service.withdraw(session, r.agent_id, record.approval_id, WITHDRAW)
    with pytest.raises(RevisionApprovalNotFound):
        await a.service.status(r.session, r.agent_id, "00000000-0000-4000-8000-00000000dead")
    await r.agents.update_one({"id": r.agent_id}, {"$set": {"creator": "someone-else"}})
    with pytest.raises(RevisionTargetNotFound):
        await a.service.status(r.session, r.agent_id, record.approval_id)
    # No Agent, System Citizen or generic signal route can approve.
    from src.domains.agentverse.registries import synaptic_registry_instance

    signals = synaptic_registry_instance.keys()  # Registry is not iterable
    assert not [s for s in signals if "revis" in s or "approv" in s]


# --- Crash recovery -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_and_commit_crashes_are_reconciled_by_replay(appr):
    a, r = appr, appr.r
    append = a.audit.append
    a.audit.append = AsyncMock(side_effect=LogUnavailable("down"))
    with pytest.raises(RevisionApprovalUnavailable):
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    a.audit.append = append
    transition = a.repository.transition
    a.repository.transition = AsyncMock(side_effect=RevisionApprovalUnavailable("crash"))
    with pytest.raises(RevisionApprovalUnavailable):
        await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    # Evidence exists, but the record is still pending: not effective.
    approvals = await r.mongo.get_collection(APPROVALS_COLLECTION)
    (pending,) = await approvals.find({}, {"_id": 0}).to_list(None)
    assert pending["status"] == "pending" and len(await events(a, APPROVED)) == 1
    a.repository.transition = transition
    record = await a.service.approve(r.session, r.agent_id, approve_request(a.review))
    assert record.status == "approved" and len(await events(a, APPROVED)) == 1
    # Withdrawal takes effect before its evidence; a failed audit is completed by replay.
    a.audit.append = AsyncMock(side_effect=LogUnavailable("down"))
    with pytest.raises(RevisionApprovalUnavailable):
        await a.service.withdraw(r.session, r.agent_id, record.approval_id, WITHDRAW)
    status = await a.service.status(r.session, r.agent_id, record.approval_id)
    assert (status["state"], status["effective"]) == ("withdrawn", False)
    a.audit.append = append
    withdrawn = await a.service.withdraw(r.session, r.agent_id, record.approval_id, WITHDRAW)
    assert withdrawn.withdrawal_event_id is not None and len(await events(a, WITHDRAWN)) == 1


# --- Projection and routes ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_resources_never_reach_the_review_projection(appr):
    r = appr.r
    draft, _ = await r.service.create_draft(
        r.session,
        r.agent_id,
        create(
            tools=[{"key": "foreign_tool", "config": {}}, {"key": "conversation", "config": {}}]
        ),
        "draft-key-0000000010",
    )
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    text = json.dumps(review.envelope)
    assert review.envelope["validation"]["resolved"] is None
    assert "tenant-b" not in text and "ownership" not in text and "llm_type" not in text
    assert {i["message"] for i in review.envelope["validation"]["issues"]} == {
        "Configuration is invalid or unavailable for this requester"
    }


@pytest.mark.asyncio
async def test_routes_expose_only_human_safe_decisions(appr, monkeypatch):
    a, r = appr, appr.r

    @asynccontextmanager
    async def session(_request):
        yield r.session

    monkeypatch.setattr(routes, "authenticated_genesis", session)
    monkeypatch.setattr(routes, "_token", lambda _request: "token")
    monkeypatch.setattr(routes, "_check_permissions", AsyncMock())
    r.env.sessions.resource_requester.return_value = User(
        id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["agentverse-user"]
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.state.agent_definition_revisions = r.service
    app.state.agent_definition_revision_approvals = a.service
    base = f"/api/v2/agentverse/agents/{r.agent_id}/definition-revisions/approvals"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        created = await http.post(base, json=approve_request(a.review).model_dump(mode="json"))
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["state"] == "approved"
        assert not {
            "authorization",
            "executor_id",
            "gateway_fingerprint",
            "utu_policy_revision",
        } & set(body)
        status = await http.get(f"{base}/{body['approval_id']}")
        assert status.json()["effective"] is True
        withdrawn = await http.post(
            f"{base}/{body['approval_id']}/withdrawal", json=WITHDRAW.model_dump(mode="json")
        )
        assert withdrawn.json()["state"] == "withdrawn"
        missing = await http.get(f"{base}/00000000-0000-4000-8000-00000000dead")
        assert missing.status_code == 404
