"""Human approval of one exact reviewed Agent draft (G3.3).

An approval records a human decision about an immutable reviewed snapshot. It is
evidence for a later, separately authorized creation step (G3.4); it never grants
creation, activation, grants or any runtime authority by itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationRequest

APPROVAL_CONTRACT = "caos.agent_creation_review_approval"
APPROVALS_COLLECTION = "coll_agent_creation_review_approvals"
# How long a human decision remains usable by a later creation step.
APPROVAL_TTL = timedelta(hours=24)

IdempotencyKey = Annotated[
    str, StringConstraints(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
ApprovalStatus = Literal["pending", "approved", "revoked"]


class ApprovalError(Exception):
    """Typed approval failure; messages never contain draft or snapshot content."""

    code = "approval.error"
    retryable = False


class ReviewStale(ApprovalError):
    code = "review.stale"


class ReviewNotEligible(ApprovalError):
    code = "review.not_eligible"


class IdempotencyConflict(ApprovalError):
    code = "idempotency.conflict"


class ApprovalExpired(ApprovalError):
    code = "approval.expired"


class ApprovalRevoked(ApprovalError):
    code = "approval.revoked"


class ApprovalNotFound(ApprovalError):
    code = "approval.not_found"


class ApprovalNotEffective(ApprovalError):
    """Not approved, or its mandatory Seshat evidence is missing or inconsistent."""

    code = "approval.not_effective"


class ApprovalConsumed(ApprovalError):
    """The approval already authorized another creation request."""

    code = "approval.consumed"


class CreationUnavailable(ApprovalError):
    """Creation outcome or its evidence is not confirmed: repeat with the same key."""

    code = "creation.unavailable"
    retryable = True


class ApprovalUnavailable(ApprovalError):
    """Persistence or audit outcome unknown: repeat with the same idempotency key."""

    code = "approval.unavailable"
    retryable = True


class CorruptApproval(ApprovalError):
    code = "approval.corrupt"


class ReviewApprovalRequest(BaseModel):
    """Explicit human approval of the exact draft whose review digest the client holds."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_creation_review_approval"]
    schema_version: StrictInt = Field(ge=1, le=1)
    draft: AgentDraftValidationRequest
    review_digest: Digest
    decision: Literal["approve"]
    confirmed: Literal[True]
    idempotency_key: IdempotencyKey

    def request_digest(self, source_agent_id: str) -> str:
        """Same key, same request: replays must match this exactly."""
        return sha256_hex(
            {
                "source_agent_id": source_agent_id,
                "draft": self.draft.model_dump(mode="json"),
                "review_digest": self.review_digest,
                "decision": self.decision,
            }
        )


def approval_id(tenant_id: str, subject_id: str, idempotency_key: str) -> str:
    """Stable identity: one requester's key always names the same approval."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-creation-review:"
            + sha256_hex(
                {"tenant_id": tenant_id, "subject_id": subject_id, "key": idempotency_key}
            ),
        )
    )


class DraftReviewApproval(BaseModel):
    """Durable record. ``snapshot`` is immutable after insert and bound by
    ``review_digest``; only status, audit and revocation fields ever change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    approval_id: str
    tenant_id: str
    subject_id: str
    source_agent_id: str
    executor_id: str
    request_digest: Digest
    review_digest: Digest
    normalized_digest: Digest
    snapshot: dict[str, Any]
    # The verified decision-time provenance, reused so a recovered approval records
    # identical Seshat evidence (it is one-way evidence, never a permit).
    authorization: AuthorizationProvenance
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    approval_event_id: str | None = None
    revoked_at: datetime | None = None
    # Provenance of the session that revoked it, never the decision-time permit.
    revocation_authorization: AuthorizationProvenance | None = None
    revocation_event_id: str | None = None
    # The single creation request this approval was consumed by (G3.4). Consumption
    # never grants creation; it only prevents reuse for another creation.
    consumed_by: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if sha256_hex(self.snapshot) != self.review_digest:
            raise ValueError("Reviewed snapshot does not match its digest")
        if self.snapshot.get("normalized_digest") != self.normalized_digest or (
            self.snapshot.get("tenant_id"),
            self.snapshot.get("requester", {}).get("subject_id"),
            self.snapshot.get("source_agent_id"),
            self.snapshot.get("executor_id"),
        ) != (self.tenant_id, self.subject_id, self.source_agent_id, self.executor_id):
            raise ValueError("Approval identity differs from its reviewed snapshot")
        if (self.authorization.tenant_id, self.authorization.subject_id) != (
            self.tenant_id,
            self.subject_id,
        ):
            raise ValueError("Approval provenance differs from its requester")
        approved = self.approved_at is not None and self.approval_event_id is not None
        if (self.approved_at is None) != (self.approval_event_id is None):
            raise ValueError("Approval time and audit evidence are recorded together")
        if self.status == "approved" and not approved:
            raise ValueError("Approved state requires its audit evidence")
        if self.status == "pending" and (approved or self.revoked_at is not None):
            raise ValueError("A pending approval has no decision or revocation")
        if (self.status == "revoked") != (self.revoked_at is not None):
            raise ValueError("Only a revoked approval has a revocation time")
        if self.expires_at.astimezone(UTC) <= self.created_at.astimezone(UTC):
            raise ValueError("An approval expires after it is requested")
        if self.approved_at is not None and self.approved_at.astimezone(
            UTC
        ) >= self.expires_at.astimezone(UTC):
            raise ValueError("An approval can only take effect before it expires")
        revocation = self.revocation_authorization
        if revocation is not None and (
            self.status != "revoked"
            or (revocation.tenant_id, revocation.subject_id) != (self.tenant_id, self.subject_id)
        ):
            raise ValueError("Revocation provenance must be the requester's, after revocation")
        if self.revocation_event_id is not None and revocation is None:
            raise ValueError("Revocation evidence requires its own provenance")
        if self.consumed_by is not None and self.approved_at is None:
            raise ValueError("Only an approval that took effect can be consumed")
        return self

    def state(self, now: datetime) -> Literal["pending", "approved", "revoked", "expired"]:
        """Effective state. Only ``approved`` before expiry is a usable decision."""
        if self.status == "revoked":
            return "revoked"
        if now.astimezone(UTC) >= self.expires_at.astimezone(UTC):
            return "expired"
        return self.status

    def public(self, now: datetime) -> dict[str, Any]:
        """Client projection: no snapshot, provenance or internal identities."""

        def stamp(value: datetime | None) -> str | None:
            if value is None:
                return None
            return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

        return {
            "contract": APPROVAL_CONTRACT,
            "schema_version": 1,
            "approval_id": self.approval_id,
            "source_agent_id": self.source_agent_id,
            "state": self.state(now),
            "review_digest": self.review_digest,
            "approved_at": stamp(self.approved_at),
            "expires_at": stamp(self.expires_at),
            "revoked_at": stamp(self.revoked_at),
            "audit_event_id": self.approval_event_id,
            "consumed": self.consumed_by is not None,
            "authorizes": {"creation": False, "activation": False},
        }
