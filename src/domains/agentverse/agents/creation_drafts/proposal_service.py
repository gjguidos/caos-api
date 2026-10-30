"""Trusted coordinator for draft proposals and their human decisions (DRAFT-0.2A).

Submission: a verified proposer (the draft's own source Agent, acting in the draft
owner's session) stores a typed proposal against a committed revision. Nothing about
the draft changes.

Decision: the draft owner, freshly authorized exactly as for any draft write, claims
the proposal (compare-and-set ``pending`` → ``deciding``) with one exact decision.
An acceptance then commits draft revision ``n + 1`` through the DRAFT-0.1 revision CAS,
carrying a ``cause`` that names the proposal and decision; Seshat evidence is recorded;
the proposal is finalized. The revision insert is the single atomic gate: a retry or a
concurrent identical request finds a committed acceptance by its cause, so a proposal
is applied at most once, and a claim is released only when its acceptance can no longer
commit (revision or field conflict). Nothing here reviews, approves, creates or
activates an Agent, calls an LLM or stores a permit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from src.domains.agentverse.agents.creation_drafts.models import (
    MAX_PAGE,
    AgentDraftContent,
    AgentDraftRevision,
    DraftInvalidRequest,
    DraftLocked,
    DraftNotFound,
    FieldProvenance,
    RevisionCause,
    RevisionConflict,
    decode_cursor,
    encode_cursor,
    pointer,
)
from src.domains.agentverse.agents.creation_drafts.proposal_repository import (
    MongoAgentDraftProposalRepository,
)
from src.domains.agentverse.agents.creation_drafts.proposals import (
    CLAIM_LEASE,
    FINAL,
    PROPOSAL_TTL,
    AgentDraftProposal,
    AgentDraftProposalRequest,
    CorruptProposal,
    DecisionClaim,
    ProposalAlreadyDecided,
    ProposalAuthor,
    ProposalDecisionInProgress,
    ProposalDecisionRequest,
    ProposalExpired,
    ProposalFieldConflict,
    ProposalIdempotencyConflict,
    ProposalInvalid,
    ProposalNotFound,
    ProposalOutcome,
    ProposalUnavailable,
    fingerprint,
    proposal_id,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_drafts.service import (
    AgentDraftService,
    Authorize,
    Owner,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.audit.models import (
    EventType,
    LogCorrelation,
    LogEvent,
    LogSubject,
    LogTarget,
    stable_event_id,
)
from src.domains.agentverse.audit.repository import LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

ACCEPTED: Final = "agent.draft.proposal.accepted"
REJECTED: Final = "agent.draft.proposal.rejected"
# A claim CAS can be lost to a concurrent decision; each loss is re-evaluated, bounded.
_ATTEMPTS = 3


def _apply(
    content: Mapping[str, Any], changes: Mapping[str, Any], remove: AbstractSet[str]
) -> dict[str, Any]:
    merged = {**content, **changes}
    return AgentDraftContent.model_validate(
        {field: value for field, value in merged.items() if field not in remove}
    ).data()


class AgentDraftProposalService:
    def __init__(
        self,
        *,
        drafts: AgentDraftService,
        revisions: MongoAgentDraftRepository,
        proposals: MongoAgentDraftProposalRepository,
        audit: AuditSink,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = PROPOSAL_TTL,
        lease: timedelta = CLAIM_LEASE,
    ) -> None:
        self._drafts = drafts
        self._revisions = revisions
        self._proposals = proposals
        self._audit = audit
        self._clock = clock
        self._ttl = ttl
        self._lease = lease

    def now(self) -> datetime:
        return self._clock()

    # --- Submission (verified proposer) ---------------------------------------------------

    async def propose(
        self, author: ProposalAuthor, request: AgentDraftProposalRequest, idempotency_key: str
    ) -> tuple[AgentDraftProposal, bool]:
        """Store one proposal, or replay it. Only the draft's own source Agent, in the
        draft owner's session, may propose; the draft is never changed."""
        draft = await self._revisions.current(author.tenant_id, request.draft_id)
        if (
            draft is None
            or draft.owner_subject_id != author.subject_id
            or draft.source_agent_id != author.agent_id
        ):
            raise DraftNotFound("Draft not found")
        if draft.status != "editing":
            raise DraftLocked(draft.status, draft.revision)
        identity = proposal_id(
            author.tenant_id, author.subject_id, author.agent_id, draft.draft_id, idempotency_key
        )
        digest = request.request_digest()
        stored = await self._proposals.get(author.tenant_id, identity)
        if stored is not None:
            if stored.request_digest != digest:
                raise ProposalIdempotencyConflict("Idempotency key already used for a proposal")
            return stored, False
        if request.base_revision > draft.revision:
            raise ProposalInvalid("The base revision does not exist")
        base = (
            draft
            if request.base_revision == draft.revision
            else await self._revisions.revision(
                author.tenant_id, draft.draft_id, request.base_revision
            )
        )
        if base is None:
            raise ProposalInvalid("The base revision does not exist")
        changes = request.changes.data()
        remove = set(request.remove)
        try:
            proposed = _apply(base.content, changes, remove)
        except ValueError as exc:
            raise ProposalInvalid("The proposed draft is not valid draft content") from exc
        if proposed == base.content:
            raise ProposalInvalid("The proposal changes nothing at its base revision")
        now = self._clock()
        record = AgentDraftProposal.build(
            proposal_id=identity,
            draft_id=draft.draft_id,
            tenant_id=draft.tenant_id,
            owner_subject_id=draft.owner_subject_id,
            source_agent_id=draft.source_agent_id,
            origin="enki",
            proposer_agent_id=author.agent_id,
            authorization=author.authorization,
            base_revision=base.revision,
            base_fingerprints={
                field: fingerprint(base.content, field) for field in set(changes) | remove
            },
            changes=changes,
            remove=sorted(remove),
            rationale=request.rationale,
            request_digest=digest,
            status="pending",
            created_at=now,
            expires_at=now + self._ttl,
        )
        stored, created = await self._proposals.insert(record)
        if not created and stored.request_digest != digest:
            raise ProposalIdempotencyConflict("Idempotency key already used for a proposal")
        return stored, created

    # --- Human operations -----------------------------------------------------------------

    async def list_proposals(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        *,
        state: str,
        limit: int,
        cursor: str | None,
        authorize_creator: Authorize,
    ) -> tuple[list[AgentDraftProposal], AgentDraftRevision, str | None]:
        """The owner's proposals for one of their drafts, newest first, paginated.
        ``open`` includes expired-but-undecided proposals (their state says so)."""
        statuses = {"open": ("pending", "deciding"), "decided": tuple(sorted(FINAL)), "all": None}
        if state not in statuses or not 1 <= limit <= MAX_PAGE:
            raise DraftInvalidRequest("Invalid listing")
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        draft = await self._drafts.owned(owner, source_agent_id, draft_id)
        scope = {"proposals": [owner.tenant_id, owner.subject_id, draft.draft_id], "state": state}
        page, following = await self._proposals.list_for_draft(
            owner.tenant_id,
            owner.subject_id,
            draft.draft_id,
            statuses=statuses[state],
            limit=limit,
            after=decode_cursor(cursor, scope),
        )
        proposals = [p for p in page if p.source_agent_id == draft.source_agent_id]
        return proposals, draft, None if following is None else encode_cursor(following, scope)

    async def get(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        proposal_id: str,
        *,
        authorize_creator: Authorize,
    ) -> tuple[AgentDraftProposal, AgentDraftRevision]:
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        draft = await self._drafts.owned(owner, source_agent_id, draft_id)
        proposal = await self._owned(owner, draft, proposal_id)
        if proposal.status == "deciding":
            # Complete an acceptance interrupted after its draft revision committed.
            caused = await self._caused(proposal, draft)
            if caused is not None:
                try:
                    proposal = await self._finalize_caused(proposal, caused, None)
                except ProposalUnavailable:
                    pass  # Still committed; the next read or retry records the evidence.
        return proposal, draft

    async def decide(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        proposal_id: str,
        request: ProposalDecisionRequest,
        *,
        authorize_creator: Authorize,
    ) -> tuple[AgentDraftProposal, AgentDraftRevision]:
        """Apply the owner's explicit decision once; identical requests replay it."""
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        draft = await self._drafts.owned(owner, source_agent_id, draft_id)
        proposal = await self._owned(owner, draft, proposal_id)
        if not set(request.fields) <= proposal.fields():
            raise ProposalInvalid("Only proposed fields can be accepted")
        if (
            request.decision == "accept"
            and draft.status != "editing"
            and proposal.status == "pending"
        ):
            raise DraftLocked(draft.status, draft.revision)
        digest = request.decision_digest(proposal.proposal_id)
        for _ in range(_ATTEMPTS):
            if proposal.status in FINAL:
                assert proposal.outcome is not None
                if proposal.outcome.decision_digest != digest:
                    raise ProposalAlreadyDecided("The proposal was already decided")
                return proposal, await self._current(draft)
            caused = await self._caused(proposal, draft)
            if caused is not None:
                return await self._finalize_caused(proposal, caused, digest), await self._current(
                    draft
                )
            now = self._clock()
            if proposal.state(now) == "expired":
                raise ProposalExpired("The proposal expired")
            if proposal.claim is not None and proposal.claim.decision_digest != digest:
                if now < proposal.claim.lease_expires_at:
                    raise ProposalDecisionInProgress("Another decision holds this proposal")
                # The holder stopped: complete its decision first, then re-evaluate.
                proposal, draft = await self._recover(owner, proposal, draft)
                continue
            if proposal.status == "pending":
                if request.decision == "accept" and request.expected_revision != draft.revision:
                    # Checked before claiming: a claim's expected revision is never in the
                    # future, so once released it can never commit (see _recover).
                    raise RevisionConflict(draft.revision)
                claim = DecisionClaim(
                    decision=request.decision,
                    fields=sorted(request.fields),
                    expected_revision=request.expected_revision,
                    decision_digest=digest,
                    subject_id=owner.subject_id,
                    authorization=owner.authorization,
                    claimed_at=now,
                    attempt=1,
                    lease_expires_at=now + self._lease,
                )
                proposal = await self._proposals.transition(
                    proposal,
                    expected="pending",
                    claim_digest=None,
                    changes={"status": "deciding", "claim": claim},
                )
                if proposal.claim is None or proposal.claim.decision_digest != digest:
                    draft = await self._current(draft)
                    continue  # Lost to a concurrent decision: re-evaluate.
            return await self._execute(owner, proposal, draft)
        raise ProposalUnavailable("The proposal is contended; repeat the request")

    # --- Internals --------------------------------------------------------------------------

    async def _owned(
        self, owner: Owner, draft: AgentDraftRevision, proposal_id: str
    ) -> AgentDraftProposal:
        proposal = await self._proposals.get(owner.tenant_id, proposal_id)
        # Another owner's, tenant's, source's or draft's proposal is indistinguishable.
        if (
            proposal is None
            or proposal.tenant_id != owner.tenant_id
            or proposal.owner_subject_id != owner.subject_id
            or proposal.source_agent_id != draft.source_agent_id
            or proposal.draft_id != draft.draft_id
        ):
            raise ProposalNotFound("Proposal not found")
        return proposal

    async def _current(self, draft: AgentDraftRevision) -> AgentDraftRevision:
        current = await self._revisions.current(draft.tenant_id, draft.draft_id)
        if current is None:
            raise ProposalUnavailable("Draft is unavailable")
        return current

    async def _caused(
        self, proposal: AgentDraftProposal, draft: AgentDraftRevision
    ) -> AgentDraftRevision | None:
        """The committed revision this proposal's acceptance caused, if any. Only a
        claim holder commits, so pending proposals are not scanned."""
        if proposal.status != "deciding":
            return None
        for number in range(draft.revision, proposal.base_revision, -1):
            revision = (
                draft
                if number == draft.revision
                else await self._revisions.revision(draft.tenant_id, draft.draft_id, number)
            )
            if revision is None:
                raise ProposalUnavailable("Draft revision is unavailable")
            if revision.cause is not None and revision.cause.proposal_id == proposal.proposal_id:
                return revision
        return None

    async def _execute(
        self, owner: Owner, proposal: AgentDraftProposal, draft: AgentDraftRevision
    ) -> tuple[AgentDraftProposal, AgentDraftRevision]:
        claim = proposal.claim
        assert claim is not None
        if claim.decision == "reject":
            outcome = self._outcome(proposal, claim, accepted=[], revision=None)
            return await self._finalize(proposal, outcome), draft
        if draft.status != "editing":
            # Frozen after the claim's revision: its target revision is taken.
            await self._release(proposal)
            raise DraftLocked(draft.status, draft.revision)
        if draft.revision != claim.expected_revision:
            await self._release(proposal)
            raise RevisionConflict(draft.revision)
        selected = set(claim.fields)
        conflicting = proposal.conflicts(draft, selected)
        if conflicting:
            await self._release(proposal)
            every = proposal.conflicts(draft, proposal.fields())
            raise ProposalFieldConflict(
                conflicting,
                sorted(proposal.fields() - set(every)),
                draft.revision,
            )
        changes: dict[str, Any] = {
            field: value for field, value in proposal.changes.items() if field in selected
        }
        content = _apply(draft.content, changes, selected & set(proposal.remove))
        if content == draft.content:
            # Already the draft's values: an accepted decision with no new revision.
            outcome = self._outcome(proposal, claim, accepted=sorted(selected), revision=draft)
            return await self._finalize(proposal, outcome), draft
        if proposal.state(self._clock()) == "expired":
            raise ProposalExpired("The proposal expired")
        number = draft.revision + 1
        record = AgentDraftRevision.build(
            draft_id=draft.draft_id,
            revision=number,
            tenant_id=draft.tenant_id,
            owner_subject_id=draft.owner_subject_id,
            source_agent_id=draft.source_agent_id,
            status=draft.status,
            content=content,
            provenance={
                pointer(field): (
                    FieldProvenance(
                        origin="proposal", revision=number, proposal_id=proposal.proposal_id
                    )
                    if field in selected and draft.content.get(field) != value
                    else draft.provenance[pointer(field)]
                )
                for field, value in content.items()
            },
            creation_request_digest=draft.creation_request_digest,
            parent_digest=draft.revision_digest,
            forked_from=draft.forked_from,
            created_at=draft.created_at,
            committed_at=max(self._clock(), draft.committed_at),
            cause=RevisionCause(
                kind="proposal",
                proposal_id=proposal.proposal_id,
                decision_digest=claim.decision_digest,
                accepted_fields=sorted(selected),
            ),
        )
        stored, committed = await self._revisions.commit(record)
        if not committed:
            if stored.cause is not None and stored.cause.proposal_id == proposal.proposal_id:
                # An identical concurrent request committed this acceptance first.
                return await self._finalize_caused(proposal, stored, claim.decision_digest), stored
            await self._release(proposal)
            raise RevisionConflict((await self._current(draft)).revision)
        return await self._finalize_caused(proposal, stored, claim.decision_digest), stored

    async def _recover(
        self, owner: Owner, proposal: AgentDraftProposal, draft: AgentDraftRevision
    ) -> tuple[AgentDraftProposal, AgentDraftRevision]:
        """Take over an expired claim and complete the claimed decision itself.

        Exactly one recoverer wins the takeover (CAS on the claim attempt). The claimed
        decision runs exactly as its own retry would, so a still-running original is an
        identical twin: the revision insert and the caused-revision scan apply it at most
        once, and its claim is released only when it can no longer commit.
        """
        claim = proposal.claim
        assert claim is not None
        now = self._clock()
        taken = await self._proposals.transition(
            proposal,
            expected="deciding",
            claim_digest=claim.decision_digest,
            claim_attempt=claim.attempt,
            changes={
                "claim": claim.model_copy(
                    update={"attempt": claim.attempt + 1, "lease_expires_at": now + self._lease}
                )
            },
        )
        if (
            taken.claim is None
            or taken.claim.attempt != claim.attempt + 1
            or (taken.claim.decision_digest != claim.decision_digest)
        ):
            return taken, await self._current(draft)  # Another recoverer or a decision won.
        try:
            await self._execute(owner, taken, draft)
        except (RevisionConflict, ProposalFieldConflict, DraftLocked):
            pass  # The claimed acceptance could not commit and was released.
        stored = await self._proposals.get(taken.tenant_id, taken.proposal_id)
        if stored is None:
            raise CorruptProposal("Proposal disappeared during recovery")
        return stored, await self._current(draft)

    async def _release(self, proposal: AgentDraftProposal) -> None:
        """Return a claim whose acceptance can no longer commit, so the human may decide
        again (e.g. accept only the applicable fields)."""
        assert proposal.claim is not None
        await self._proposals.transition(
            proposal,
            expected="deciding",
            claim_digest=proposal.claim.decision_digest,
            changes={"status": "pending", "claim": None},
        )

    def _outcome(
        self,
        proposal: AgentDraftProposal,
        claim: DecisionClaim,
        *,
        accepted: list[str],
        revision: AgentDraftRevision | None,
    ) -> ProposalOutcome:
        event = ACCEPTED if claim.decision == "accept" else REJECTED
        return ProposalOutcome(
            decision=claim.decision,
            accepted_fields=sorted(accepted),  # type: ignore[arg-type]
            rejected_fields=sorted(proposal.fields() - set(accepted)),  # type: ignore[arg-type]
            decision_digest=claim.decision_digest,
            resulting_revision=None if revision is None else revision.revision,
            content_digest=None if revision is None else revision.content_digest,
            decided_by=claim.subject_id,
            # The claim time, so every retry produces identical evidence.
            decided_at=claim.claimed_at,
            event_id=stable_event_id(
                correlation_id=proposal.proposal_id, event_type=event, step="decision"
            ),
        )

    async def _finalize_caused(
        self, proposal: AgentDraftProposal, caused: AgentDraftRevision, digest: str | None
    ) -> AgentDraftProposal:
        cause = caused.cause
        assert cause is not None
        if proposal.status not in FINAL:
            claim = proposal.claim
            if (
                claim is None
                or claim.decision_digest != cause.decision_digest
                or cause.accepted_fields is None
            ):
                raise CorruptProposal("A committed acceptance does not match its claim")
            outcome = self._outcome(
                proposal, claim, accepted=list(cause.accepted_fields), revision=caused
            )
            proposal = await self._finalize(proposal, outcome)
        if digest is not None and cause.decision_digest != digest:
            raise ProposalAlreadyDecided("The proposal was already decided")
        return proposal

    async def _finalize(
        self, proposal: AgentDraftProposal, outcome: ProposalOutcome
    ) -> AgentDraftProposal:
        """Record mandatory evidence, then close the claim. Built only from stored
        fields, so a retry appends the identical event and makes the same transition."""
        claim = proposal.claim
        assert claim is not None
        event_type: EventType = ACCEPTED if outcome.decision == "accept" else REJECTED
        event = LogEvent(
            event_id=outcome.event_id,
            event_type=event_type,
            outcome="succeeded",
            subject=LogSubject(resource_type="agent"),
            target=LogTarget(signal=DATA_VALIDATION_SIGNAL, version=DATA_VALIDATION_VERSION),
            correlation=LogCorrelation(
                correlation_id=proposal.draft_id, causation_id=proposal.proposal_id
            ),
            metadata={
                "event_version": 1,
                "proposal_id": proposal.proposal_id,
                "draft_id": proposal.draft_id,
                "source_agent_id": proposal.source_agent_id,
                "proposer_agent_id": proposal.proposer_agent_id,
                "base_revision": proposal.base_revision,
                "resulting_revision": outcome.resulting_revision,
                "accepted_fields": list(outcome.accepted_fields),
                "rejected_fields": list(outcome.rejected_fields),
                "proposal_digest": proposal.proposal_digest,
                "decision_digest": outcome.decision_digest,
                "content_digest": outcome.content_digest,
            },
        )
        try:
            await self._audit.append(event, authorization=claim.authorization)
        except LogError as exc:
            raise ProposalUnavailable(
                "Decision evidence is unavailable; repeat the request"
            ) from exc
        status = (
            "rejected"
            if outcome.decision == "reject"
            else "accepted"
            if set(outcome.accepted_fields) == proposal.fields()
            else "partially_accepted"
        )
        stored = await self._proposals.transition(
            proposal,
            expected="deciding",
            claim_digest=claim.decision_digest,
            changes={"status": status, "outcome": outcome},
        )
        if stored.outcome is None or stored.outcome.decision_digest != claim.decision_digest:
            raise ProposalUnavailable("The decision did not take effect; repeat the request")
        return stored
