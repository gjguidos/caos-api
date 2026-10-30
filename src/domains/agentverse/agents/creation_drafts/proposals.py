"""Structured proposals against an Agent creation draft (DRAFT-0.2A).

A proposal is a typed set of field changes against one committed draft revision,
submitted by a verified proposer (Enki, acting in its human's session). It never
changes the draft: only an explicit, freshly authorized human decision applies some or
all of its fields as a new draft revision. A proposal is never a review, approval,
creation or activation authority.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self, get_args
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.agents.creation_drafts.models import (
    AgentDraftContent,
    AgentDraftRevision,
    DraftError,
    DraftField,
)
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.revocation import ExecutionPermit

PROPOSAL_CONTRACT = "caos.agent_draft_proposal"
PROPOSALS_COLLECTION = "coll_agent_draft_proposals"
# A proposal is a suggestion about one moment of a conversation, not a standing offer.
PROPOSAL_TTL = timedelta(hours=24)
# How long one attempt owns a decision before another request may recover it.
CLAIM_LEASE = timedelta(seconds=60)
# Enki's scoped proposal capability (capability.py); see AGENT_DRAFTS.md §9.
PROPOSE_SIGNAL = "agent.draft.propose"

ProposalOrigin = Literal["enki"]
ProposalStatus = Literal["pending", "deciding", "accepted", "partially_accepted", "rejected"]
Decision = Literal["accept", "reject"]
FINAL: frozenset[str] = frozenset({"accepted", "partially_accepted", "rejected"})
IdempotencyKey = Annotated[
    str, StringConstraints(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
_FIELDS = len(get_args(DraftField))


class ProposalNotFound(DraftError):
    """Missing, or for another owner, tenant, source or draft: indistinguishable."""

    code = "proposal.not_found"


class ProposalInvalid(DraftError):
    code = "proposal.invalid"


class ProposalIdempotencyConflict(DraftError):
    code = "idempotency.conflict"


class ProposalExpired(DraftError):
    code = "proposal.expired"


class ProposalAlreadyDecided(DraftError):
    code = "proposal.already_decided"


class ProposalDecisionInProgress(DraftError):
    """Another decision holds the proposal; only an identical request may complete it."""

    code = "proposal.decision_in_progress"


class ProposalFieldConflict(DraftError):
    """Selected fields changed after the proposal's base revision; nothing was applied."""

    code = "proposal.field_conflict"

    def __init__(
        self, conflicting: list[str], applicable: list[str], current_revision: int
    ) -> None:
        super().__init__("Selected fields changed after the proposal's base revision")
        self.conflicting = conflicting
        self.applicable = applicable
        self.current_revision = current_revision


class ProposalUnavailable(DraftError):
    """Persistence or evidence outcome unknown: repeat the identical request."""

    code = "proposal.unavailable"
    retryable = True


class CorruptProposal(DraftError):
    code = "proposal.corrupt"


@dataclass(frozen=True)
class ProposalAuthor:
    """A verified proposer: an Agent acting in its human's live session. Built only from
    a verified UTU permit; the permit and session identifier are not retained."""

    tenant_id: str
    subject_id: str
    agent_id: str
    authorization: AuthorizationProvenance

    @classmethod
    def from_permit(cls, permit: ExecutionPermit) -> ProposalAuthor:
        if permit.origin != "session":
            raise EventPermissionError("Proposals require an Agent in a live human session")
        key = permit.key
        return cls(
            key.tenant_id, key.user_id, key.agent_id, permit_provenance(permit, PROPOSE_SIGNAL)
        )


class AgentDraftProposalRequest(BaseModel):
    """What a proposer may submit: typed changes against one committed revision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_proposal_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    draft_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    base_revision: StrictInt = Field(ge=1)
    changes: AgentDraftContent = Field(default_factory=AgentDraftContent)
    remove: list[DraftField] = Field(default_factory=list, max_length=_FIELDS)
    # Display only; never applied, audited or treated as an instruction.
    rationale: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if len(set(self.remove)) != len(self.remove):
            raise ValueError("Duplicate removed field")
        if set(self.remove) & set(self.changes.data()):
            raise ValueError("A field cannot be both changed and removed")
        if not self.remove and not self.changes.data():
            raise ValueError("A proposal changes at least one field")
        return self

    def request_digest(self) -> str:
        return sha256_hex(
            self.model_dump(mode="json", exclude={"changes"}) | {"changes": self.changes.data()}
        )


class ProposalDecisionRequest(BaseModel):
    """An explicit human decision. ``accept`` names the selected fields and the draft
    revision the human saw; ``reject`` declines the whole proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_proposal_decision"]
    schema_version: StrictInt = Field(ge=1, le=1)
    decision: Decision
    confirmed: Literal[True]
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    fields: list[DraftField] = Field(default_factory=list, max_length=_FIELDS)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Duplicate selected field")
        if self.decision == "accept" and (not self.fields or self.expected_revision is None):
            raise ValueError("Acceptance selects fields at an expected draft revision")
        if self.decision == "reject" and (self.fields or self.expected_revision is not None):
            raise ValueError("A rejection declines the whole proposal")
        return self

    def decision_digest(self, proposal_id: str) -> str:
        return sha256_hex(
            {
                "proposal_id": proposal_id,
                "decision": self.decision,
                "fields": sorted(self.fields),
                "expected_revision": self.expected_revision,
            }
        )


class DecisionClaim(BaseModel):
    """The one decision that currently holds a proposal (status ``deciding``).

    Ownership is bounded by ``lease_expires_at``: after it, any authorized request may
    take the claim over (``attempt`` + 1, compare-and-set) and completes *this* decision,
    never its own, so a stale original attempt is an identical twin and the draft
    revision CAS still applies the proposal at most once.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    fields: list[DraftField]
    expected_revision: StrictInt | None
    decision_digest: Digest
    subject_id: str
    authorization: AuthorizationProvenance
    claimed_at: datetime
    attempt: StrictInt = Field(ge=1)
    lease_expires_at: datetime

    @model_validator(mode="after")
    def _lease(self) -> Self:
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("A claim lease ends after the claim")
        return self


class ProposalOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    accepted_fields: list[DraftField]
    rejected_fields: list[DraftField]
    decision_digest: Digest
    resulting_revision: StrictInt | None
    content_digest: Digest | None
    decided_by: str
    decided_at: datetime
    event_id: str


def proposal_id(
    tenant_id: str, subject_id: str, agent_id: str, draft_id: str, idempotency_key: str
) -> str:
    """Stable identity: one proposer's key for one draft always names the same proposal."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-draft-proposal:"
            + sha256_hex(
                {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "agent_id": agent_id,
                    "draft_id": draft_id,
                    "key": idempotency_key,
                }
            ),
        )
    )


def fingerprint(content: dict[str, Any], field: str) -> Digest | None:
    """Value fingerprint of one field; ``None`` when absent."""
    return None if field not in content else sha256_hex({"value": content[field]})


class AgentDraftProposal(BaseModel):
    """Durable proposal. Content fields are immutable after insert; only ``status``,
    ``claim`` and ``outcome`` change, by compare-and-set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    proposal_id: str
    draft_id: str
    tenant_id: str
    owner_subject_id: str
    source_agent_id: str
    origin: ProposalOrigin
    proposer_agent_id: str
    authorization: AuthorizationProvenance
    base_revision: StrictInt = Field(ge=1)
    base_fingerprints: dict[DraftField, Digest | None]
    changes: dict[str, Any]
    remove: list[DraftField]
    rationale: str | None
    request_digest: Digest
    proposal_digest: Digest
    status: ProposalStatus
    claim: DecisionClaim | None = None
    outcome: ProposalOutcome | None = None
    created_at: datetime
    expires_at: datetime

    @classmethod
    def build(cls, **fields: Any) -> AgentDraftProposal:
        unsigned = cls.model_construct(**fields, proposal_digest="0" * 64)
        return cls.model_validate({**fields, "proposal_digest": unsigned.digest()})

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if AgentDraftContent.model_validate(self.changes).data() != self.changes:
            raise ValueError("Proposal changes are not canonical draft content")
        if set(self.base_fingerprints) != self.fields():
            raise ValueError("Every proposed field has exactly one base fingerprint")
        if self.proposal_digest != self.digest():
            raise ValueError("Proposal digest mismatch")
        if (self.status == "deciding") != (self.claim is not None and self.outcome is None):
            raise ValueError("Only a deciding proposal holds an open claim")
        if (self.status in FINAL) != (self.outcome is not None):
            raise ValueError("Only a decided proposal has an outcome")
        if self.expires_at <= self.created_at:
            raise ValueError("A proposal expires after it is created")
        return self

    def fields(self) -> set[str]:
        return set(self.changes) | set(self.remove)

    def digest(self) -> str:
        """Content fingerprint of what was proposed, against which base."""
        return sha256_hex(
            {
                "proposal_id": self.proposal_id,
                "draft_id": self.draft_id,
                "base_revision": self.base_revision,
                "base_fingerprints": self.base_fingerprints,
                "changes": self.changes,
                "remove": sorted(self.remove),
            }
        )

    def state(self, now: datetime) -> str:
        if self.status in FINAL:
            return self.status
        return "expired" if now >= self.expires_at else self.status

    def conflicts(self, current: AgentDraftRevision, fields: AbstractSet[str]) -> list[str]:
        """Selected fields whose value or provenance changed after the base revision."""
        return sorted(
            field
            for field in fields
            if fingerprint(current.content, field) != self.base_fingerprints[field]  # type: ignore[index]
            or (
                "/" + field in current.provenance
                and current.provenance["/" + field].revision > self.base_revision
            )
        )

    def public(self, now: datetime, current: AgentDraftRevision | None = None) -> dict[str, Any]:
        """Human projection: the proposed change and, against the current draft, which
        fields still apply. No tenant, owner, storage identity or evidence."""
        fields = sorted(self.fields())
        conflicting = [] if current is None else self.conflicts(current, set(fields))
        return {
            "contract": PROPOSAL_CONTRACT,
            "schema_version": 1,
            "proposal_id": self.proposal_id,
            "draft_id": self.draft_id,
            "source_agent_id": self.source_agent_id,
            "origin": self.origin,
            "proposer_agent_id": self.proposer_agent_id,
            "base_revision": self.base_revision,
            "changes": dict(self.changes),
            "remove": sorted(self.remove),
            "rationale": self.rationale,
            "state": self.state(now),
            "fields": fields,
            "current_revision": None if current is None else current.revision,
            "conflicting_fields": conflicting,
            "applicable_fields": [f for f in fields if f not in conflicting],
            "outcome": None
            if self.outcome is None
            else {
                "decision": self.outcome.decision,
                "accepted_fields": list(self.outcome.accepted_fields),
                "rejected_fields": list(self.outcome.rejected_fields),
                "resulting_revision": self.outcome.resulting_revision,
                "decided_at": _timestamp(self.outcome.decided_at),
            },
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
            # A proposal, or its acceptance, never authorizes these.
            "authorizes": {
                "review": False,
                "approval": False,
                "creation": False,
                "activation": False,
            },
        }


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
