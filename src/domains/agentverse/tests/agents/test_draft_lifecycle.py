"""DRAFT-0.2B: listing, exact-revision review, approval binding and freeze, reopen,
confirmed consumption and fork, over the real G3.3 gateway and approval service.

Storage is the in-memory MongoDB-like doubles (real Mongo:
test_draft_lifecycle_integration.py). G3.4 creation records are a read-only stub,
because only their confirmed state matters to the draft lifecycle.
"""

import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.domains.agentverse.agents.creation_drafts.lifecycle import (
    AgentDraftLifecycleService,
    DraftApprovalRequest,
    DraftReopenRequest,
)
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    REVISIONS_COLLECTION,
    AgentDraftCreateRequest,
    AgentDraftForkRequest,
    DraftCreationInProgress,
    DraftInvalidRequest,
    DraftLocked,
    DraftNotFound,
    RevisionConflict,
    draft_id,
)
from src.domains.agentverse.agents.creation_review.models import (
    ApprovalConsumed,
    ReviewStale,
)
from src.domains.agentverse.agents.creation_review.repository import (
    MongoDraftReviewApprovalRepository,
)
from src.domains.agentverse.agents.creation_review.service import (
    REVOKED,
    DraftReviewApprovalService,
)
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationRequest
from src.domains.agentverse.tests.agents.test_creation_drafts import (  # noqa: F401
    allow,
    create_body,
    created,
    drafts,
    update,
)
from src.domains.agentverse.tests.agents.test_creation_review import ApprovalMongo
from src.domains.agentverse.tests.agents.test_draft_proposals import (  # noqa: F401
    REJECT,
    accept,
    proposals,
    proposed,
)
from src.domains.agentverse.tests.agents.test_validation_gateway import (  # noqa: F401
    request,
    stack,
)
from src.domains.agentverse.tests.audit.test_caos_log import service as seshat


class Creations:
    """Read-only G3.4 creation records by ID (what the lifecycle consults)."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    async def get(self, tenant_id: str, creation_id: str) -> Any:
        record = self.records.get(creation_id)
        return record if record is not None and record.tenant_id == tenant_id else None

    def put(self, creation_id: str, approval_id: str, state: str, agent_id: str | None = None):
        self.records[creation_id] = SimpleNamespace(
            creation_id=creation_id,
            tenant_id=KEY.tenant_id,
            approval_id=approval_id,
            state=state,
            agent_id=agent_id,
        )


@pytest.fixture
def lifecycle(proposals):  # noqa: F811
    s = proposals
    s.approval_audit = seshat()
    s.approvals = DraftReviewApprovalService(
        gateway=s.gateway,
        repository=MongoDraftReviewApprovalRepository(ApprovalMongo()),
        audit=s.approval_audit.service,
        clock=s.clock,
    )
    s.creations = Creations()
    s.lifecycle = AgentDraftLifecycleService(
        drafts=s.drafts,
        revisions=s.repository,
        gateway=s.gateway,
        approvals=s.approvals,
        creations=s.creations,  # type: ignore[arg-type]
        clock=s.clock,
    )
    return s


def complete_body() -> AgentDraftCreateRequest:
    """The existing eligible G3.3 test definition, as draft content."""
    return AgentDraftCreateRequest.model_validate(request().model_dump(mode="json"))


async def reviewed(s, draft):
    envelope = await s.lifecycle.review(
        s.env.session, "source", draft.draft_id, draft.revision, authorize_creator=allow
    )
    assert envelope["eligibility"]["eligible"], envelope
    return envelope


def approval_body(revision, digest, key="draft-approval-key-01"):
    return DraftApprovalRequest.model_validate(
        {
            "contract": "caos.agent_draft_approval",
            "schema_version": 1,
            "revision": revision,
            "review_digest": digest,
            "decision": "approve",
            "confirmed": True,
            "idempotency_key": key,
        }
    )


async def approved(s, key="draft-approval-key-01"):
    draft = await created(s, complete_body())
    envelope = await reviewed(s, draft)
    approval, frozen = await s.lifecycle.approve(
        s.env.session,
        "source",
        draft.draft_id,
        approval_body(draft.revision, envelope["review_digest"], key),
        authorize_creator=allow,
    )
    return draft, approval, frozen


def reopen_body(expected):
    return DraftReopenRequest.model_validate(
        {"contract": "caos.agent_draft_reopen", "schema_version": 1, "expected_revision": expected}
    )


async def consume(s, approval, creation_id="creation-0001"):
    effective = await s.approvals.effective(KEY.tenant_id, KEY.user_id, approval.approval_id)
    return await s.approvals.consume(effective, creation_id)


def revisions(s):
    return s.store.documents(REVISIONS_COLLECTION)


# --- Listing ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_listing_is_owner_scoped_newest_first_and_paginated(lifecycle):
    s = lifecycle
    mine = [await created(s, key=f"draft-key-list-{n:05d}") for n in range(5)]
    # Another owner's draft in the same tenant and source is never listed.
    other = copy.deepcopy(s.store.documents(DRAFTS_COLLECTION)[mine[0].draft_id])
    other.update({"_id": "x", "draft_id": "x", "owner_subject_id": "intruder"})
    s.store.documents(DRAFTS_COLLECTION)["x"] = other
    seen, cursor = [], None
    while True:
        page, cursor = await s.drafts.list_drafts(
            s.env.session, "source", listing="open", limit=2, cursor=cursor, authorize_creator=allow
        )
        seen += [d.draft_id for d in page]
        if cursor is None:
            break
    assert seen == [d.draft_id for d in reversed(mine)]
    with pytest.raises(DraftInvalidRequest):
        await s.drafts.list_drafts(
            s.env.session,
            "source",
            listing="open",
            limit=2,
            cursor="tampered",
            authorize_creator=allow,
        )
    _, cursor = await s.drafts.list_drafts(
        s.env.session, "source", listing="open", limit=2, cursor=None, authorize_creator=allow
    )
    with pytest.raises(DraftInvalidRequest):  # A cursor continues only its own query.
        await s.drafts.list_drafts(
            s.env.session, "source", listing="all", limit=2, cursor=cursor, authorize_creator=allow
        )
    for limit in (0, 51):
        with pytest.raises(DraftInvalidRequest):
            await s.drafts.list_drafts(
                s.env.session,
                "source",
                listing="open",
                limit=limit,
                cursor=None,
                authorize_creator=allow,
            )
    indexes = s.store.collections[DRAFTS_COLLECTION].indexes
    assert "draft_owner_recent" in indexes


@pytest.mark.asyncio
async def test_proposal_listing_is_per_draft_owner_scoped_and_filtered(lifecycle):
    s = lifecycle
    draft = await created(s)
    other = await created(s, key="draft-key-000000009")
    ids = [
        (await proposed(s, draft, {"prompt": f"P{n}"}, key=f"proposal-list-{n:08d}")).proposal_id
        for n in range(3)
    ]
    await proposed(s, other, {"prompt": "Elsewhere"}, key="proposal-other-00001")
    await s.proposals.decide(
        s.env.session, "source", draft.draft_id, ids[0], REJECT, authorize_creator=allow
    )
    page, current, cursor = await s.proposals.list_proposals(
        s.env.session,
        "source",
        draft.draft_id,
        state="open",
        limit=1,
        cursor=None,
        authorize_creator=allow,
    )
    rest, _, end = await s.proposals.list_proposals(
        s.env.session,
        "source",
        draft.draft_id,
        state="open",
        limit=5,
        cursor=cursor,
        authorize_creator=allow,
    )
    assert [p.proposal_id for p in page + rest] == [ids[2], ids[1]] and end is None
    decided, _, _ = await s.proposals.list_proposals(
        s.env.session,
        "source",
        draft.draft_id,
        state="decided",
        limit=5,
        cursor=None,
        authorize_creator=allow,
    )
    assert [p.proposal_id for p in decided] == [ids[0]] and current.draft_id == draft.draft_id
    with pytest.raises(DraftNotFound):
        await s.proposals.list_proposals(
            s.env.session,
            "source",
            draft_id("t", "u", "s", "k" * 16),
            state="all",
            limit=5,
            cursor=None,
            authorize_creator=allow,
        )


# --- Exact-revision review -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_review_is_server_read_and_keeps_the_authoritative_digest(lifecycle):
    s = lifecycle
    draft = await created(s, complete_body())
    envelope = await reviewed(s, draft)
    direct = await s.gateway.validate(s.env.session, "source", request())
    assert envelope["review_digest"] == direct["review_digest"]
    assert envelope["draft_ref"] == {
        "draft_id": draft.draft_id,
        "revision": 1,
        "content_digest": draft.content_digest,
    }
    edited, _ = await update(s, draft, 1, {"prompt": "Changed after review"})
    again = await reviewed(s, edited)
    assert again["review_digest"] != envelope["review_digest"]
    # Revision 1 is immutable: reviewing it again reproduces the original digest.
    assert (await reviewed(s, draft))["review_digest"] == envelope["review_digest"]


# --- Approval binding and freeze ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_binds_the_reviewed_revision_and_freezes_the_draft(lifecycle):
    s = lifecycle
    draft, approval, frozen = await approved(s)
    assert approval.status == "approved" and approval.snapshot["draft"]["contract"]
    assert (frozen.status, frozen.revision) == ("approved", 2)
    assert frozen.content == draft.content and frozen.content_digest == draft.content_digest
    assert frozen.lifecycle() == {
        "approval_id": approval.approval_id,
        "review_digest": approval.review_digest,
        "reviewed_revision": 1,
        "creation_id": None,
        "agent_id": None,
    }
    with pytest.raises(DraftLocked) as locked:
        await update(s, frozen, 2, {"prompt": "Edit after approval"})
    assert (locked.value.code, locked.value.current_revision) == ("draft.approved", 2)
    proposal_target = frozen
    from src.domains.agentverse.tests.agents.test_draft_proposals import author, proposal_body

    with pytest.raises(DraftLocked):
        await s.proposals.propose(
            author(s),
            proposal_body(proposal_target.draft_id, 2, {"prompt": "X"}),
            "proposal-frozen-0001",
        )
    # Replay of the same approval returns the same frozen state; another is refused.
    replay = await s.lifecycle.approve(
        s.env.session,
        "source",
        draft.draft_id,
        approval_body(1, approval.review_digest),
        authorize_creator=allow,
    )
    assert replay[1] == frozen and replay[0].approval_id == approval.approval_id
    with pytest.raises(DraftLocked):
        await s.lifecycle.approve(
            s.env.session,
            "source",
            draft.draft_id,
            approval_body(2, approval.review_digest, "draft-approval-key-02"),
            authorize_creator=allow,
        )


@pytest.mark.asyncio
async def test_approval_requires_the_current_revision_and_the_current_digest(lifecycle):
    s = lifecycle
    draft = await created(s, complete_body())
    envelope = await reviewed(s, draft)
    edited, _ = await update(s, draft, 1, {"prompt": "Edited after review"})
    with pytest.raises(RevisionConflict):
        await s.lifecycle.approve(
            s.env.session,
            "source",
            draft.draft_id,
            approval_body(1, envelope["review_digest"]),
            authorize_creator=allow,
        )
    with pytest.raises(ReviewStale):  # G3.3 re-review: the digest names other content.
        await s.lifecycle.approve(
            s.env.session,
            "source",
            draft.draft_id,
            approval_body(2, envelope["review_digest"]),
            authorize_creator=allow,
        )
    assert (
        await s.lifecycle.get(s.env.session, "source", draft.draft_id, authorize_creator=allow)
    ).status == "editing"
    assert edited.revision == 2


@pytest.mark.asyncio
async def test_an_edit_racing_the_approval_withdraws_it_and_conflicts(lifecycle):
    s = lifecycle
    draft = await created(s, complete_body())
    envelope = await reviewed(s, draft)
    original = s.approvals.approve

    async def racing(*args, **kwargs):
        result = await original(*args, **kwargs)
        await update(s, draft, 1, {"prompt": "Edited in another tab"})
        return result

    s.approvals.approve = racing  # type: ignore[method-assign]
    with pytest.raises(RevisionConflict) as raised:
        await s.lifecycle.approve(
            s.env.session,
            "source",
            draft.draft_id,
            approval_body(1, envelope["review_digest"]),
            authorize_creator=allow,
        )
    assert raised.value.current_revision == 2
    stored = list(s.approvals._repository.repository.collection.documents.values())
    assert [a["status"] for a in stored] == ["revoked"]
    assert [e["event_type"] for e in s.approval_audit.mongo.collection.documents.values()].count(
        REVOKED
    ) == 1
    current = await s.lifecycle.get(
        s.env.session, "source", draft.draft_id, authorize_creator=allow
    )
    assert current.status == "editing" and current.content["prompt"] == "Edited in another tab"


@pytest.mark.asyncio
async def test_concurrent_edits_and_approval_leave_exactly_one_consistent_outcome(lifecycle):
    s = lifecycle
    draft = await created(s, complete_body())
    envelope = await reviewed(s, draft)
    results = await asyncio.gather(
        s.lifecycle.approve(
            s.env.session,
            "source",
            draft.draft_id,
            approval_body(1, envelope["review_digest"]),
            authorize_creator=allow,
        ),
        update(s, draft, 1, {"prompt": "Concurrent edit"}),
        return_exceptions=True,
    )
    current = await s.lifecycle.get(
        s.env.session, "source", draft.draft_id, authorize_creator=allow
    )
    approvals = list(s.approvals._repository.repository.collection.documents.values())
    if current.status == "approved":
        assert isinstance(results[1], RevisionConflict | DraftLocked)
        assert current.content == draft.content and approvals[0]["status"] == "approved"
    else:
        assert isinstance(results[0], RevisionConflict | ReviewStale)
        assert all(a["status"] == "revoked" for a in approvals)


# --- Reopen and consumption --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_revokes_an_unconsumed_approval_and_requires_a_new_review(lifecycle):
    s = lifecycle
    draft, approval, _ = await approved(s)
    reopened = await s.lifecycle.reopen(
        s.env.session, "source", draft.draft_id, reopen_body(2), authorize_creator=allow
    )
    assert (reopened.status, reopened.revision, reopened.cause.kind) == ("editing", 3, "reopen")
    assert reopened.content_digest == draft.content_digest
    stored = await s.approvals.owned(KEY.tenant_id, KEY.user_id, approval.approval_id)
    assert stored.status == "revoked"
    assert (
        await s.lifecycle.reopen(
            s.env.session, "source", draft.draft_id, reopen_body(3), authorize_creator=allow
        )
        == reopened
    )  # Idempotent.
    edited, _ = await update(s, reopened, 3, {"prompt": "Edited after reopening"})
    assert edited.revision == 4


@pytest.mark.asyncio
async def test_reopen_never_interferes_with_a_creation_in_progress(lifecycle):
    s = lifecycle
    draft, approval, _ = await approved(s)
    await consume(s, approval)
    for state in (None, "accepted", "provisioning"):
        if state:
            s.creations.put("creation-0001", approval.approval_id, state)
        with pytest.raises(DraftCreationInProgress):
            await s.lifecycle.reopen(
                s.env.session, "source", draft.draft_id, reopen_body(2), authorize_creator=allow
            )
    stored = await s.approvals.owned(KEY.tenant_id, KEY.user_id, approval.approval_id)
    assert stored.status == "approved" and stored.consumed_by == "creation-0001"
    # A failed creation has spent the approval: the human may reopen and start again.
    s.creations.put("creation-0001", approval.approval_id, "failed")
    reopened = await s.lifecycle.reopen(
        s.env.session, "source", draft.draft_id, reopen_body(2), authorize_creator=allow
    )
    assert reopened.status == "editing"


@pytest.mark.asyncio
async def test_unconsumed_revocation_is_atomic_against_consumption(lifecycle):
    s = lifecycle
    _, approval, _ = await approved(s)
    await consume(s, approval)
    with pytest.raises(ApprovalConsumed):
        await s.approvals.revoke(
            s.env.session, "source", approval.approval_id, require_unconsumed=True
        )
    stored = await s.approvals.owned(KEY.tenant_id, KEY.user_id, approval.approval_id)
    assert stored.status == "approved"


@pytest.mark.asyncio
async def test_confirmed_creation_is_recorded_once_as_content_unchanged_consumption(lifecycle):
    s = lifecycle
    draft, approval, _ = await approved(s)
    await consume(s, approval)
    s.creations.put("creation-0001", approval.approval_id, "provisioning")
    pending = await s.lifecycle.get(
        s.env.session, "source", draft.draft_id, authorize_creator=allow
    )
    assert pending.status == "approved"  # Not confirmed yet: nothing recorded.
    s.creations.put("creation-0001", approval.approval_id, "completed", agent_id="agent-0001")
    results = await asyncio.gather(
        *[
            s.lifecycle.get(s.env.session, "source", draft.draft_id, authorize_creator=allow)
            for _ in range(5)
        ]
    )
    assert len({r.revision for r in results}) == 1
    consumed = results[0]
    assert (consumed.status, consumed.revision) == ("consumed", 3)
    assert consumed.content_digest == draft.content_digest
    assert consumed.lifecycle()["agent_id"] == "agent-0001"
    assert consumed.lifecycle()["review_digest"] == approval.review_digest
    assert len(revisions(s)) == 3
    for operation in (
        lambda: update(s, consumed, 3, {"prompt": "X"}),
        lambda: s.lifecycle.reopen(
            s.env.session, "source", draft.draft_id, reopen_body(3), authorize_creator=allow
        ),
    ):
        with pytest.raises(DraftLocked) as locked:
            await operation()
        assert locked.value.code == "draft.consumed"
    listed, _ = await s.drafts.list_drafts(
        s.env.session,
        "source",
        listing="consumed",
        limit=5,
        cursor=None,
        authorize_creator=allow,
        reconcile=s.lifecycle.reconcile,
    )
    assert [d.draft_id for d in listed] == [draft.draft_id]


# --- Fork --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_copies_an_owned_revision_into_an_isolated_editing_draft(lifecycle):
    s = lifecycle
    draft, _, frozen = await approved(s)
    body = AgentDraftForkRequest.model_validate(
        {"contract": "caos.agent_draft_fork", "schema_version": 1, "revision": 1}
    )
    fork, made = await s.drafts.fork(
        s.env.session,
        "source",
        draft.draft_id,
        body,
        "fork-key-0000000001",
        authorize_creator=allow,
    )
    again, made_again = await s.drafts.fork(
        s.env.session,
        "source",
        draft.draft_id,
        body,
        "fork-key-0000000001",
        authorize_creator=allow,
    )
    assert made and not made_again and again == fork
    assert (fork.status, fork.revision, fork.content) == ("editing", 1, draft.content)
    assert fork.forked_from.model_dump() == {"draft_id": draft.draft_id, "revision": 1}
    edited, _ = await update(s, fork, 1, {"prompt": "Only the fork changes"})
    assert edited.forked_from == fork.forked_from
    original = await s.lifecycle.get(
        s.env.session, "source", draft.draft_id, authorize_creator=allow
    )
    assert original == frozen
    with pytest.raises(DraftNotFound):
        await s.drafts.fork(
            s.env.session,
            "source",
            draft_id("t", "u", "s", "k" * 16),
            body,
            "fork-key-0000000002",
            authorize_creator=allow,
        )


# --- Boundaries -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_creates_activates_and_grants_nothing(lifecycle):
    s = lifecycle
    _, approval, frozen = await approved(s)
    assert approval.public(s.approvals.now())["authorizes"] == {
        "creation": False,
        "activation": False,
    }
    assert frozen.public()["authorizes"] == dict.fromkeys(
        ("review", "approval", "creation", "activation"), False
    )
    # Only draft collections changed; lifecycle revisions record no Seshat event.
    assert set(s.store.collections) <= {
        DRAFTS_COLLECTION,
        REVISIONS_COLLECTION,
        "coll_agent_draft_proposals",
    }
    assert list(s.audit.mongo.collection.documents.values()) == []
    body = AgentDraftValidationRequest.model_validate(frozen.creation_draft())
    assert (await s.gateway.review(s.env.session, "source", body)).digest == approval.review_digest


@pytest.mark.asyncio
async def test_proposal_acceptance_is_refused_on_a_frozen_draft(lifecycle):
    s = lifecycle
    draft = await created(s, complete_body())
    proposal = await proposed(s, draft, {"prompt": "From Enki"})
    envelope = await reviewed(s, draft)
    await s.lifecycle.approve(
        s.env.session,
        "source",
        draft.draft_id,
        approval_body(1, envelope["review_digest"]),
        authorize_creator=allow,
    )
    with pytest.raises(DraftLocked):
        await s.proposals.decide(
            s.env.session,
            "source",
            draft.draft_id,
            proposal.proposal_id,
            accept(["prompt"], 2),
            authorize_creator=allow,
        )
    decided, _ = await s.proposals.decide(
        s.env.session,
        "source",
        draft.draft_id,
        proposal.proposal_id,
        REJECT,
        authorize_creator=allow,
    )
    assert decided.status == "rejected"


@pytest.mark.asyncio
async def test_an_expired_approval_keeps_the_draft_frozen_until_reopened(lifecycle):
    s = lifecycle
    draft, approval, frozen = await approved(s)
    s.clock.now += timedelta(hours=25)
    still = await s.lifecycle.get(s.env.session, "source", draft.draft_id, authorize_creator=allow)
    assert still == frozen  # Expiry alone changes nothing; the UI offers reopening.
    reopened = await s.lifecycle.reopen(
        s.env.session, "source", draft.draft_id, reopen_body(2), authorize_creator=allow
    )
    assert reopened.status == "editing"
    stored = await s.approvals.owned(KEY.tenant_id, KEY.user_id, approval.approval_id)
    assert stored.status == "approved" and stored.state(s.clock()) == "expired"
