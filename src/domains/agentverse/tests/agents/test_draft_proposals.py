"""DRAFT-0.2A proposals and human decisions: validation, isolation, selective and
conflicting acceptance, idempotent replay, interrupted writes, concurrency, Seshat
evidence and the absence of any review, approval, creation or activation effect.

Real UTU/CPBX/validator gateway stack and Seshat service; storage is in-memory
MongoDB-like doubles (real Mongo: test_draft_proposals_integration.py).
"""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.lifecycle import AgentDraftLifecycleService
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    REVISIONS_COLLECTION,
    AgentDraftRevision,
    CorruptDraft,
    DraftNotFound,
    DraftUnavailable,
    RevisionConflict,
)
from src.domains.agentverse.agents.creation_drafts.proposal_repository import (
    MongoAgentDraftProposalRepository,
)
from src.domains.agentverse.agents.creation_drafts.proposal_service import (
    ACCEPTED,
    REJECTED,
    AgentDraftProposalService,
)
from src.domains.agentverse.agents.creation_drafts.proposals import (
    PROPOSALS_COLLECTION,
    AgentDraftProposalRequest,
    ProposalAlreadyDecided,
    ProposalAuthor,
    ProposalDecisionInProgress,
    ProposalDecisionRequest,
    ProposalExpired,
    ProposalFieldConflict,
    ProposalIdempotencyConflict,
    ProposalInvalid,
    ProposalNotFound,
    ProposalUnavailable,
)
from src.domains.agentverse.agents.creation_drafts.repository import (
    revision_key,
    to_document,
)
from src.domains.agentverse.interfaces.api.v2 import genesis_agents
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationRequest
from src.domains.agentverse.tests.agents.test_creation_drafts import (  # noqa: F401
    CREATOR,
    DraftCollection,
    allow,
    create_body,
    created,
    deny,
    drafts,
    update,
)
from src.domains.agentverse.tests.agents.test_validation_gateway import (  # noqa: F401
    request,
    stack,
)
from src.domains.agentverse.tests.audit.test_caos_log import service as seshat

PROPOSAL_KEY = "proposal-key-00000001"


class ProposalCollection(DraftCollection):
    """Adds the compare-and-set ``update_one`` (dotted claim filter) proposals use."""

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any:
        self.calls.append("update_one")
        await asyncio.sleep(0)
        row = self.documents.get(query["_id"])

        def value(document: dict[str, Any], path: str) -> Any:
            for part in path.split("."):
                document = (document or {}).get(part) if isinstance(document, dict) else None
            return document

        matched = row is not None and all(
            value(row, key) == expected for key, expected in query.items() if key != "_id"
        )
        if matched:
            row.update(copy.deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=int(matched))


@pytest.fixture
def proposals(drafts):  # noqa: F811
    s = drafts
    s.store.collections[PROPOSALS_COLLECTION] = ProposalCollection()
    s.audit = seshat()
    s.proposals = AgentDraftProposalService(
        drafts=s.drafts,
        revisions=s.repository,
        proposals=MongoAgentDraftProposalRepository(s.store),
        audit=s.audit.service,
        clock=s.drafts._clock,
    )
    s.clock = s.drafts._clock
    return s


def author(s, *, agent_id="source", subject_id=None, tenant_id=None) -> ProposalAuthor:
    """The source Agent in the owner's session (as the future CPBX provider builds it)."""
    permit = SimpleNamespace(
        key=replace(
            KEY,
            agent_id=agent_id,
            user_id=subject_id or KEY.user_id,
            tenant_id=tenant_id or KEY.tenant_id,
        ),
        versions=("policy:1",),
        origin="session",
    )
    return ProposalAuthor.from_permit(permit)  # type: ignore[arg-type]


def proposal_body(draft_id, base, changes=None, remove=(), **extra) -> AgentDraftProposalRequest:
    return AgentDraftProposalRequest.model_validate(
        {
            "contract": "caos.agent_draft_proposal_request",
            "schema_version": 1,
            "draft_id": draft_id,
            "base_revision": base,
            "changes": changes or {},
            "remove": list(remove),
            **extra,
        }
    )


def accept(fields, expected) -> ProposalDecisionRequest:
    return ProposalDecisionRequest.model_validate(
        {
            "contract": "caos.agent_draft_proposal_decision",
            "schema_version": 1,
            "decision": "accept",
            "confirmed": True,
            "expected_revision": expected,
            "fields": list(fields),
        }
    )


REJECT = ProposalDecisionRequest.model_validate(
    {
        "contract": "caos.agent_draft_proposal_decision",
        "schema_version": 1,
        "decision": "reject",
        "confirmed": True,
    }
)


async def proposed(s, draft, changes=None, remove=(), *, base=None, key=PROPOSAL_KEY):
    record, _ = await s.proposals.propose(
        author(s),
        proposal_body(
            draft.draft_id,
            base or draft.revision,
            changes or {"prompt": "Summarize contracts", "role_name": "general"},
            remove,
        ),
        key,
    )
    return record


async def decide(s, draft, proposal, decision, *, authz=allow, session=None):
    return await s.proposals.decide(
        session or s.env.session,
        "source",
        draft.draft_id,
        proposal.proposal_id,
        decision,
        authorize_creator=authz,
    )


def events(s, event_type=None):
    rows = list(s.audit.mongo.collection.documents.values())
    return [r for r in rows if event_type is None or r["event_type"] == event_type]


def revisions(s):
    return s.store.documents(REVISIONS_COLLECTION)


# --- Phase 1: DRAFT-0.1 recovery verification ------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_readers_repair_a_lagging_projection_once(proposals):
    s = proposals
    record = await created(s)
    s.store.collections[DRAFTS_COLLECTION].replace_error = AutoReconnect("lost")
    second, _ = await update(s, record, 1, {"prompt": "Committed"})
    reads = await asyncio.gather(
        *[s.repository.current(KEY.tenant_id, record.draft_id) for _ in range(6)]
    )
    assert all(read == second for read in reads)
    assert s.store.documents(DRAFTS_COLLECTION)[record.draft_id]["revision"] == 2


@pytest.mark.asyncio
async def test_a_broken_revision_chain_fails_closed_on_roll_forward(proposals):
    s = proposals
    record = await created(s)
    forged = AgentDraftRevision.build(
        **{
            **record.model_dump(exclude={"revision_digest", "content_digest"}),
            "revision": 2,
            "parent_digest": "0" * 64,
            "content": {"name": "Forged"},
            "provenance": {"/name": {"origin": "user", "revision": 2}},
        }
    )
    revisions(s)[revision_key(record.draft_id, 2)] = to_document(
        forged, revision_key(record.draft_id, 2)
    )
    with pytest.raises(CorruptDraft):
        await s.repository.current(KEY.tenant_id, record.draft_id)


def test_draft_0_1_revision_digests_are_unchanged_by_the_optional_cause():
    from src.domains.agentverse.agents.creation_drafts.models import FieldProvenance

    assert FieldProvenance(origin="user", revision=1).model_dump() == {
        "origin": "user",
        "revision": 1,
    }
    with pytest.raises(ValidationError):
        FieldProvenance(origin="proposal", revision=1)
    with pytest.raises(ValidationError):
        FieldProvenance(origin="user", revision=1, proposal_id="p")


@pytest.mark.asyncio
async def test_unchanged_revisions_serialize_without_cause(proposals):
    record = await created(proposals)
    assert "cause" not in record.model_dump(mode="json")
    assert "cause" not in revisions(proposals)[revision_key(record.draft_id, 1)]


# --- Proposal submission ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_proposal_is_typed_attributed_idempotent_and_never_changes_the_draft(proposals):
    s = proposals
    draft = await created(s)
    before = copy.deepcopy(revisions(s))
    proposal = await proposed(s, draft)
    assert (proposal.status, proposal.base_revision, proposal.origin) == ("pending", 1, "enki")
    assert (proposal.owner_subject_id, proposal.proposer_agent_id) == (KEY.user_id, "source")
    assert proposal.authorization.principal_id == "source"
    assert proposal.base_fingerprints == {"prompt": None, "role_name": None}
    assert proposal.expires_at - proposal.created_at == timedelta(hours=24)
    replay, created_again = await s.proposals.propose(
        author(s),
        proposal_body(draft.draft_id, 1, {"prompt": "Summarize contracts", "role_name": "general"}),
        PROPOSAL_KEY,
    )
    assert replay == proposal and not created_again
    with pytest.raises(ProposalIdempotencyConflict):
        await proposed(s, draft, {"prompt": "Something else"})
    assert revisions(s) == before
    assert events(s) == []  # Submission is not a consequential decision.


@pytest.mark.asyncio
async def test_only_the_source_agent_for_its_own_human_may_propose(proposals):
    s = proposals
    draft = await created(s)
    body = proposal_body(draft.draft_id, 1, {"prompt": "X"})
    for stranger in (
        author(s, agent_id="other-agent"),
        author(s, subject_id="other-human"),
        author(s, tenant_id="other-tenant"),
    ):
        with pytest.raises(DraftNotFound):
            await s.proposals.propose(stranger, body, PROPOSAL_KEY)
    bootstrap = SimpleNamespace(key=KEY, versions=("policy:1",), origin="bootstrap")
    with pytest.raises(EventPermissionError):
        ProposalAuthor.from_permit(bootstrap)  # type: ignore[arg-type]
    assert s.store.documents(PROPOSALS_COLLECTION) == {}


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "accepted"},
        {"owner_subject_id": "x"},
        {"tenant_id": "x"},
        {"approved": True},
        {"origin": "user"},
        {"proposal_id": "x"},
        {"changes": {"creator": "x"}},
        {"changes": {"name": 7}},
        {"changes": {}, "remove": []},
        {"changes": {"name": "A"}, "remove": ["name"]},
        {"base_revision": 0},
        {"rationale": "x" * 2001},
    ],
)
def test_proposal_requests_reject_authority_metadata_and_invalid_structures(extra):
    body = {
        "contract": "caos.agent_draft_proposal_request",
        "schema_version": 1,
        "draft_id": "d",
        "base_revision": 1,
        "changes": {"prompt": "X"},
        **extra,
    }
    with pytest.raises(ValidationError):
        AgentDraftProposalRequest.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "accept", "fields": ["prompt"]},  # No expected revision.
        {"decision": "accept", "expected_revision": 1},  # No fields.
        {"decision": "reject", "fields": ["prompt"]},
        {"decision": "reject", "expected_revision": 1},
        {"decision": "approve"},
        {"decision": "reject", "confirmed": False},
        {"decision": "reject", "approval_id": "x"},
        {"decision": "accept", "expected_revision": 1, "fields": ["prompt", "prompt"]},
        {"decision": "accept", "expected_revision": 1, "fields": ["tenant_id"]},
    ],
)
def test_decision_requests_are_strict(body):
    with pytest.raises(ValidationError):
        ProposalDecisionRequest.model_validate(
            {
                "contract": "caos.agent_draft_proposal_decision",
                "schema_version": 1,
                "confirmed": True,
                **body,
            }
        )


@pytest.mark.asyncio
async def test_unknown_base_and_no_op_proposals_are_invalid(proposals):
    s = proposals
    draft = await created(s)
    with pytest.raises(ProposalInvalid):
        await proposed(s, draft, {"prompt": "X"}, base=2)
    with pytest.raises(ProposalInvalid):
        await proposed(s, draft, {"name": draft.content["name"]})
    with pytest.raises(ProposalInvalid):
        await proposed(s, draft, remove=["prompt"], changes={"type": "chat"})


# --- Human acceptance ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_acceptance_commits_one_attributed_revision_with_evidence(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    decided, current = await decide(s, draft, proposal, accept(["prompt", "role_name"], 1))
    assert decided.status == "accepted" and decided.outcome.resulting_revision == 2
    assert current.revision == 2 and current.content["prompt"] == "Summarize contracts"
    assert current.provenance["/prompt"].model_dump() == {
        "origin": "proposal",
        "revision": 2,
        "proposal_id": proposal.proposal_id,
    }
    assert current.provenance["/name"].origin == "user"
    assert current.cause.proposal_id == proposal.proposal_id
    assert current.cause.accepted_fields == ["prompt", "role_name"]
    (event,) = events(s)
    assert event["event_type"] == ACCEPTED
    assert (
        event["correlation_id"] == draft.draft_id and event["causation_id"] == proposal.proposal_id
    )
    assert event["actor"]["user_id"] == KEY.user_id
    assert event["metadata"] == {
        "event_version": 1,
        "proposal_id": proposal.proposal_id,
        "draft_id": draft.draft_id,
        "source_agent_id": "source",
        "proposer_agent_id": "source",
        "base_revision": 1,
        "resulting_revision": 2,
        "accepted_fields": ["prompt", "role_name"],
        "rejected_fields": [],
        "proposal_digest": proposal.proposal_digest,
        "decision_digest": decided.outcome.decision_digest,
        "content_digest": current.content_digest,
    }
    # Never draft content, rationale or prompts in evidence.
    assert "Summarize contracts" not in json.dumps(event)


@pytest.mark.asyncio
async def test_identical_acceptance_replays_without_a_second_revision_or_event(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    first = await decide(s, draft, proposal, accept(["prompt"], 1))
    again = await decide(s, draft, proposal, accept(["prompt"], 1))
    assert again == first and len(revisions(s)) == 2 and len(events(s)) == 1
    with pytest.raises(ProposalAlreadyDecided):
        await decide(s, draft, proposal, accept(["prompt", "role_name"], 1))
    with pytest.raises(ProposalAlreadyDecided):
        await decide(s, draft, proposal, REJECT)


@pytest.mark.asyncio
async def test_selective_acceptance_applies_only_the_chosen_fields(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft, {"prompt": "P", "role_name": "general"}, remove=["type"])
    decided, current = await decide(s, draft, proposal, accept(["prompt", "type"], 1))
    assert decided.status == "partially_accepted"
    assert (decided.outcome.accepted_fields, decided.outcome.rejected_fields) == (
        ["prompt", "type"],
        ["role_name"],
    )
    assert current.content == {"name": "Draft Candidate", "prompt": "P"}
    assert "/role_name" not in current.provenance


@pytest.mark.asyncio
async def test_changed_fields_conflict_and_the_safe_fields_can_then_be_accepted(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft, {"prompt": "From Enki", "name": "Enki Name"})
    edited, _ = await update(s, draft, 1, {"name": "Human Name"})
    with pytest.raises(ProposalFieldConflict) as raised:
        await decide(s, draft, proposal, accept(["prompt", "name"], 2))
    assert (raised.value.conflicting, raised.value.applicable) == (["name"], ["prompt"])
    assert len(revisions(s)) == 2 and events(s) == []
    stored, _ = await s.proposals.get(
        s.env.session, "source", draft.draft_id, proposal.proposal_id, authorize_creator=allow
    )
    assert stored.status == "pending"  # Released for a new decision.
    view = stored.public(s.clock(), edited)
    assert (view["conflicting_fields"], view["applicable_fields"]) == (["name"], ["prompt"])
    decided, current = await decide(s, draft, proposal, accept(["prompt"], 2))
    assert decided.status == "partially_accepted"
    assert current.content["name"] == "Human Name" and current.content["prompt"] == "From Enki"


@pytest.mark.asyncio
async def test_a_field_changed_and_restored_still_conflicts_by_provenance(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft, {"name": "Enki Name"})
    second, _ = await update(s, draft, 1, {"name": "Other"})
    await update(s, second, 2, {"name": "Draft Candidate"})
    with pytest.raises(ProposalFieldConflict):
        await decide(s, draft, proposal, accept(["name"], 3))


@pytest.mark.asyncio
async def test_a_stale_expected_revision_conflicts_and_releases_the_claim(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    await update(s, draft, 1, {"type": "form"})
    with pytest.raises(RevisionConflict) as raised:
        await decide(s, draft, proposal, accept(["prompt"], 1))
    assert raised.value.current_revision == 2
    decided, current = await decide(s, draft, proposal, accept(["prompt"], 2))
    assert decided.status == "partially_accepted" and current.revision == 3


@pytest.mark.asyncio
async def test_rejection_is_evidenced_and_changes_nothing(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    decided, current = await decide(s, draft, proposal, REJECT)
    assert decided.status == "rejected" and current == draft
    assert decided.outcome.rejected_fields == ["prompt", "role_name"]
    (event,) = events(s, REJECTED)
    assert event["metadata"]["resulting_revision"] is None
    assert (await decide(s, draft, proposal, REJECT)) == (decided, current)
    with pytest.raises(ProposalAlreadyDecided):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    assert len(revisions(s)) == 1


@pytest.mark.asyncio
async def test_accepting_values_the_draft_already_has_commits_no_revision(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft, {"prompt": "Same"})
    current, _ = await update(s, draft, 1, {"prompt": "Same"})
    # The human typed the same value after the base: a provenance conflict, not a no-op.
    with pytest.raises(ProposalFieldConflict):
        await decide(s, draft, proposal, accept(["prompt"], 2))
    other = await proposed(
        s, current, {"prompt": "Same", "role_name": "general"}, key="proposal-key-00000002"
    )
    decided, after = await decide(s, current, other, accept(["prompt"], 2))
    assert decided.status == "partially_accepted" and after == current
    assert decided.outcome.resulting_revision == 2 and len(revisions(s)) == 2


@pytest.mark.asyncio
async def test_expired_proposals_cannot_be_decided(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.clock.now += timedelta(hours=25)
    with pytest.raises(ProposalExpired):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    with pytest.raises(ProposalExpired):
        await decide(s, draft, proposal, REJECT)
    stored, _ = await s.proposals.get(
        s.env.session, "source", draft.draft_id, proposal.proposal_id, authorize_creator=allow
    )
    assert stored.public(s.clock())["state"] == "expired"


# --- Interrupted writes and reconciliation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_outage_after_commit_is_completed_by_retry_or_read(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.audit.mongo.collection.insert_error = AutoReconnect("seshat down")
    with pytest.raises(ProposalUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    assert len(revisions(s)) == 2  # Committed; evidence pending.
    s.audit.mongo.collection.insert_error = AutoReconnect("still down")
    stored, _ = await s.proposals.get(
        s.env.session, "source", draft.draft_id, proposal.proposal_id, authorize_creator=allow
    )
    assert stored.status == "deciding"  # A read never fails because evidence is down.
    with pytest.raises(ProposalUnavailable):
        # The committed acceptance's evidence comes first, even for another decision.
        await decide(s, draft, proposal, REJECT)
    s.audit.mongo.collection.insert_error = None
    with pytest.raises(ProposalAlreadyDecided):
        # A different decision cannot undo a committed acceptance; evidence now recorded.
        await decide(s, draft, proposal, REJECT)
    stored, _ = await s.proposals.get(
        s.env.session, "source", draft.draft_id, proposal.proposal_id, authorize_creator=allow
    )
    assert stored.status == "partially_accepted"
    decided, current = await decide(s, draft, proposal, accept(["prompt"], 1))
    assert decided == stored and current.revision == 2
    assert len(events(s, ACCEPTED)) == 1 and len(revisions(s)) == 2


@pytest.mark.asyncio
async def test_a_write_lost_after_commit_is_recognized_not_repeated(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].commit_then_fail = True
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].commit_then_fail = False
    decided, current = await decide(s, draft, proposal, accept(["prompt"], 1))
    assert decided.outcome.resulting_revision == 2 == current.revision
    assert len(revisions(s)) == 2 and len(events(s)) == 1


@pytest.mark.asyncio
async def test_a_claim_interrupted_before_commit_only_resumes_identically(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("no write")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    with pytest.raises(ProposalDecisionInProgress):
        await decide(s, draft, proposal, REJECT)
    decided, current = await decide(s, draft, proposal, accept(["prompt"], 1))
    assert decided.status == "partially_accepted" and current.revision == 2


# --- Concurrency -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_identical_acceptances_apply_once(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    results = await asyncio.gather(
        *[decide(s, draft, proposal, accept(["prompt"], 1)) for _ in range(6)]
    )
    assert len({r[0].outcome.decision_digest for r in results}) == 1
    assert all(r[0].status == "partially_accepted" for r in results)
    assert len(revisions(s)) == 2 and len(events(s)) == 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_decisions_have_exactly_one_winner(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    decisions = [accept(["prompt"], 1), REJECT, accept(["prompt", "role_name"], 1)] * 2
    results = await asyncio.gather(
        *[decide(s, draft, proposal, d) for d in decisions], return_exceptions=True
    )
    winners = {r[0].outcome.decision_digest for r in results if not isinstance(r, BaseException)}
    losers = [r for r in results if isinstance(r, BaseException)]
    assert len(winners) == 1
    assert all(isinstance(r, ProposalAlreadyDecided | ProposalDecisionInProgress) for r in losers)
    stored, _ = await s.proposals.get(
        s.env.session, "source", draft.draft_id, proposal.proposal_id, authorize_creator=allow
    )
    assert stored.outcome.decision_digest in winners
    assert len(revisions(s)) == (1 if stored.status == "rejected" else 2)
    assert len(events(s)) == 1


@pytest.mark.asyncio
async def test_concurrent_acceptance_of_different_proposals_serializes_on_the_revision(proposals):
    s = proposals
    draft = await created(s)
    one = await proposed(s, draft, {"prompt": "One"}, key="proposal-key-0000000a")
    two = await proposed(s, draft, {"role_name": "general"}, key="proposal-key-0000000b")
    results = await asyncio.gather(
        decide(s, draft, one, accept(["prompt"], 1)),
        decide(s, draft, two, accept(["role_name"], 1)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, RevisionConflict) for r in results) == 1
    loser = two if isinstance(results[1], RevisionConflict) else one
    field = "role_name" if loser is two else "prompt"
    _, current = await decide(s, draft, loser, accept([field], 2))
    assert current.revision == 3 and current.content["prompt"] == "One"
    assert current.content["role_name"] == "general" and len(events(s)) == 2


@pytest.mark.asyncio
async def test_two_proposals_for_one_field_cannot_both_overwrite_it(proposals):
    s = proposals
    draft = await created(s)
    one = await proposed(s, draft, {"prompt": "One"}, key="proposal-key-0000000a")
    two = await proposed(s, draft, {"prompt": "Two"}, key="proposal-key-0000000b")
    await decide(s, draft, one, accept(["prompt"], 1))
    with pytest.raises(ProposalFieldConflict):
        await decide(s, draft, two, accept(["prompt"], 2))


# --- Isolation and authority --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposals_of_other_owners_drafts_and_sources_are_not_found(proposals):
    s = proposals
    draft = await created(s)
    other_draft = await created(s, key="draft-key-000000002")
    proposal = await proposed(s, draft)
    with pytest.raises(ProposalNotFound):
        await decide(s, other_draft, proposal, REJECT)
    rows = s.store.documents(PROPOSALS_COLLECTION)
    stored = rows[proposal.proposal_id]
    for field, value in (
        ("owner_subject_id", "intruder"),
        ("tenant_id", "other"),
        ("source_agent_id", "x"),
    ):
        original = stored[field]
        stored[field] = value
        with pytest.raises(ProposalNotFound):
            await decide(s, draft, proposal, REJECT)
        stored[field] = original
    assert events(s) == []


@pytest.mark.asyncio
async def test_human_decisions_need_fresh_creator_authority(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    with pytest.raises(EventPermissionError):
        await decide(s, draft, proposal, REJECT, authz=deny)
    for session in (
        replace(s.env.session, origin="bootstrap"),
        replace(s.env.session, active=False),
    ):
        with pytest.raises(EventPermissionError):
            await decide(s, draft, proposal, REJECT, session=session)
    s.env.sessions.resource_requester.return_value = CREATOR.model_copy(update={"id": "other"})
    with pytest.raises(EventPermissionError):
        await decide(s, draft, proposal, REJECT)
    assert s.store.documents(PROPOSALS_COLLECTION)[proposal.proposal_id]["status"] == "pending"


# --- Integrity and boundaries ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_revisions_stay_immutable_and_their_cause_is_digest_bound(proposals):
    s = proposals
    draft = await created(s)
    snapshot = copy.deepcopy(revisions(s))
    proposal = await proposed(s, draft)
    await decide(s, draft, proposal, accept(["prompt"], 1))
    assert {k: revisions(s)[k] for k in snapshot} == snapshot
    stored = revisions(s)[revision_key(draft.draft_id, 2)]
    stored["cause"]["accepted_fields"] = ["prompt", "role_name"]
    with pytest.raises(CorruptDraft):
        await s.repository.revision(KEY.tenant_id, draft.draft_id, 2)


@pytest.mark.asyncio
async def test_proposals_never_review_approve_create_or_activate(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(
        s, draft, {"prompt": "A definition", "role_name": "general", "personality_key": "empty_eva"}
    )
    _, current = await decide(
        s, draft, proposal, accept(["prompt", "role_name", "personality_key"], 1)
    )
    assert set(s.store.collections) == {
        DRAFTS_COLLECTION,
        REVISIONS_COLLECTION,
        PROPOSALS_COLLECTION,
    }
    assert {e["event_type"] for e in events(s)} == {ACCEPTED}
    assert proposal.public(s.clock())["authorizes"] == dict.fromkeys(
        ("review", "approval", "creation", "activation"), False
    )
    # G3.3 reviews an accepted draft exactly like the same body sent directly.
    body = AgentDraftValidationRequest.model_validate(current.creation_draft())
    review = await s.gateway.review(s.env.session, "source", body)
    assert review.digest == (await s.gateway.review(s.env.session, "source", body)).digest


# --- HTTP --------------------------------------------------------------------------------------------------


@pytest.fixture
def http(proposals, monkeypatch):
    s = proposals

    async def authenticated(_request):
        yield s.env.session

    monkeypatch.setattr(genesis_agents, "authenticated_genesis", asynccontextmanager(authenticated))
    app = FastAPI()
    app.container = SimpleNamespace(authz=SimpleNamespace(authz_client=lambda: None))
    app.state.agent_drafts = s.drafts
    app.state.agent_draft_lifecycle = AgentDraftLifecycleService(
        drafts=s.drafts,
        revisions=s.repository,
        gateway=s.gateway,
        approvals=None,  # type: ignore[arg-type]  # Editing drafts never reach G3.3/G3.4.
        creations=None,  # type: ignore[arg-type]
    )
    app.state.agent_draft_proposals = s.proposals
    app.include_router(genesis_agents.router)
    s.app = app
    return s


@pytest.mark.asyncio
async def test_http_proposal_view_decision_and_conflict_contract(http):
    s = http
    draft = await created(s)
    proposal = await proposed(s, draft, {"prompt": "From Enki", "name": "Enki Name"})
    await update(s, draft, 1, {"name": "Human Name"})
    base = f"/api/v2/agentverse/agents/source/creation-drafts/{draft.draft_id}/proposals"
    path = f"{base}/{proposal.proposal_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app),
        base_url="http://test",
        headers={"Authorization": "Bearer bearer-token"},
    ) as c:
        view = (await c.get(path)).json()
        assert view["state"] == "pending" and view["current_revision"] == 2
        assert (view["conflicting_fields"], view["applicable_fields"]) == (["name"], ["prompt"])
        assert not {"tenant_id", "owner_subject_id", "authorization", "claim"} & view.keys()
        conflict = await c.post(
            f"{path}/decision", json=accept(["name", "prompt"], 2).model_dump(mode="json")
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == {
            "code": "proposal.field_conflict",
            "retryable": False,
            "current_revision": 2,
            "conflicting_fields": ["name"],
            "applicable_fields": ["prompt"],
        }
        stale = await c.post(f"{path}/decision", json=accept(["prompt"], 1).model_dump(mode="json"))
        assert stale.json()["detail"]["code"] == "draft.revision_conflict"
        done = await c.post(f"{path}/decision", json=accept(["prompt"], 2).model_dump(mode="json"))
        assert done.status_code == 200, done.text
        assert done.json()["proposal"]["state"] == "partially_accepted"
        assert done.json()["draft"]["revision"] == 3
        assert (
            await c.post(f"{path}/decision", json=REJECT.model_dump(mode="json"))
        ).status_code == 409
        for bad in (
            {**REJECT.model_dump(mode="json"), "approved": True},
            {**REJECT.model_dump(mode="json"), "confirmed": False},
            {**accept(["prompt"], 3).model_dump(mode="json"), "fields": ["creator"]},
        ):
            assert (await c.post(f"{path}/decision", json=bad)).status_code == 422
        unrelated = await c.post(
            f"{path}/decision", json=accept(["type"], 3).model_dump(mode="json")
        )
        assert unrelated.json()["detail"]["code"] in (
            "proposal.invalid",
            "proposal.already_decided",
        )
        missing = f"{base}/{draft.draft_id}"
        assert (await c.get(missing)).json()["detail"]["code"] == "proposal.not_found"
        assert (await c.get(f"{base}/not-a-uuid")).status_code == 422
        s.env.sessions.resource_requester.return_value = CREATOR.model_copy(
            update={"roles": ["team.admin"]}
        )
        assert (await c.get(path)).status_code == 403


@pytest.mark.asyncio
async def test_http_has_no_proposal_submission_route(http):
    paths = {
        (getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", ()) or ())))
        for r in http.app.routes
    }
    # Listing (GET) is requester-scoped reading; nothing submits a proposal over HTTP.
    assert not any(
        path.endswith("/proposals") and {"POST", "PUT", "PATCH"} & set(methods)
        for path, methods in paths
    )
    assert any(path.endswith("/proposals") and methods == ("GET",) for path, methods in paths)


# --- DRAFT-0.2A closeout: bounded claims and recovery ------------------------------------------


def lease_expired(s):
    s.clock.now += timedelta(seconds=61)


@pytest.mark.asyncio
async def test_a_crash_before_commit_is_recovered_after_the_lease_not_after_expiry(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("crash")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    with pytest.raises(ProposalDecisionInProgress):  # Lease still owned.
        await decide(s, draft, proposal, REJECT)
    lease_expired(s)
    # The recoverer completes the claimed acceptance, never its own different decision.
    with pytest.raises(ProposalAlreadyDecided):
        await decide(s, draft, proposal, REJECT)
    stored = s.store.documents(PROPOSALS_COLLECTION)[proposal.proposal_id]
    assert stored["status"] == "partially_accepted" and stored["claim"]["attempt"] == 2
    assert len(revisions(s)) == 2 and [e["event_type"] for e in events(s)] == [ACCEPTED]
    # Evidence is attributed to the human who made the decision.
    assert events(s)[0]["authorization"]["subject_id"] == KEY.user_id


@pytest.mark.asyncio
async def test_a_recovered_claim_that_can_no_longer_commit_is_released_for_the_new_decision(
    proposals,
):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("crash")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    await update(s, draft, 1, {"type": "form"})  # The claimed acceptance is now stale.
    lease_expired(s)
    decided, current = await decide(s, draft, proposal, REJECT)
    assert decided.status == "rejected" and current.revision == 2
    assert [e["event_type"] for e in events(s)] == [REJECTED]


@pytest.mark.asyncio
async def test_a_stale_attempt_cannot_commit_after_takeover(proposals):
    s = proposals
    owner = await s.drafts.authorize(s.env.session, "source", allow)
    draft = await created(s)
    proposal = await proposed(s, draft)
    # Attempt A claims and stalls before its commit (simulated by keeping its view).
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("stall")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    stale = await s.proposals._proposals.get(KEY.tenant_id, proposal.proposal_id)
    await update(s, draft, 1, {"type": "form"})
    lease_expired(s)
    await decide(s, draft, proposal, REJECT)  # Recovery releases A's claim; rejection wins.
    before = copy.deepcopy(revisions(s))
    # A resumes with its pre-takeover view: its target revision is taken, nothing commits.
    with pytest.raises(RevisionConflict):
        await s.proposals._execute(owner, stale, draft)
    assert revisions(s) == before
    final = s.store.documents(PROPOSALS_COLLECTION)[proposal.proposal_id]
    assert final["status"] == "rejected" and len(events(s)) == 1


@pytest.mark.asyncio
async def test_a_stale_identical_attempt_after_takeover_is_a_harmless_twin(proposals):
    s = proposals
    owner = await s.drafts.authorize(s.env.session, "source", allow)
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("stall")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    stale = await s.proposals._proposals.get(KEY.tenant_id, proposal.proposal_id)
    lease_expired(s)
    with pytest.raises(ProposalAlreadyDecided):
        await decide(s, draft, proposal, REJECT)  # Recovery commits A's acceptance.
    finished, current = await s.proposals._execute(owner, stale, draft)
    assert finished.status == "partially_accepted" and current.revision == 2
    assert len(revisions(s)) == 2 and len(events(s)) == 1


@pytest.mark.asyncio
async def test_concurrent_recoveries_complete_the_claim_once(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    s.store.collections[REVISIONS_COLLECTION].insert_error = AutoReconnect("crash")
    with pytest.raises(DraftUnavailable):
        await decide(s, draft, proposal, accept(["prompt", "role_name"], 1))
    s.store.collections[REVISIONS_COLLECTION].insert_error = None
    lease_expired(s)
    results = await asyncio.gather(
        *[decide(s, draft, proposal, d) for d in (REJECT, accept(["prompt"], 1)) * 4],
        return_exceptions=True,
    )
    assert all(
        isinstance(r, ProposalAlreadyDecided | ProposalDecisionInProgress) for r in results
    ), results
    stored = s.store.documents(PROPOSALS_COLLECTION)[proposal.proposal_id]
    assert stored["claim"]["attempt"] == 2  # Exactly one takeover.
    assert stored["status"] == "accepted" and stored["outcome"]["accepted_fields"] == [
        "prompt",
        "role_name",
    ]
    assert len(revisions(s)) == 2 and len(events(s)) == 1


@pytest.mark.asyncio
async def test_a_future_expected_revision_is_refused_before_claiming(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    with pytest.raises(RevisionConflict):
        await decide(s, draft, proposal, accept(["prompt"], 5))
    assert s.store.documents(PROPOSALS_COLLECTION)[proposal.proposal_id]["status"] == "pending"


@pytest.mark.asyncio
async def test_decision_evidence_separates_human_actor_executor_proposer_and_causation(proposals):
    s = proposals
    draft = await created(s)
    proposal = await proposed(s, draft)
    await decide(s, draft, proposal, accept(["prompt"], 1))
    (event,) = events(s)
    # Actor: the deciding human (user) acting through the gateway executor (principal).
    assert event["actor"] == {"principal_id": KEY.agent_id, "user_id": KEY.user_id}
    assert event["authorization"]["subject_id"] == KEY.user_id
    # The proposing Agent and causation are recorded separately from the actor.
    assert event["metadata"]["proposer_agent_id"] == "source"
    assert (event["correlation_id"], event["causation_id"]) == (
        draft.draft_id,
        proposal.proposal_id,
    )
    assert "Summarize contracts" not in json.dumps(event)
