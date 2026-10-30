"""Trusted coordinator for human approval of an exact reviewed draft.

Order of effects: re-review the exact draft through the validation gateway, check
the requester may create Agents, persist a *pending* approval, durably record Seshat
evidence, re-check authority, then compare-and-set it to *approved*. A pending
approval is never effective, so any partial failure is recovered by repeating the
request with the same idempotency key. Nothing here writes an Agent, provisions a
grant, activates a runtime or calls an LLM.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance
from src.domains.agentverse.agents.creation_review.models import (
    APPROVAL_TTL,
    ApprovalConsumed,
    ApprovalExpired,
    ApprovalNotEffective,
    ApprovalNotFound,
    ApprovalRevoked,
    ApprovalUnavailable,
    DraftReviewApproval,
    IdempotencyConflict,
    ReviewApprovalRequest,
    ReviewNotEligible,
    ReviewStale,
    approval_id,
)
from src.domains.agentverse.agents.creation_review.repository import (
    MongoDraftReviewApprovalRepository,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.audit.models import (
    LogCorrelation,
    LogEvent,
    LogSubject,
    LogTarget,
    stable_event_id,
)
from src.domains.agentverse.audit.repository import LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationGateway
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

APPROVED = "agent.creation_review.approved"
REVOKED = "agent.creation_review.revoked"


class DraftReviewApprovalService:
    def __init__(
        self,
        *,
        gateway: AgentDraftValidationGateway,
        repository: MongoDraftReviewApprovalRepository,
        audit: AuditSink,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = APPROVAL_TTL,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._ttl = ttl

    async def approve(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        request: ReviewApprovalRequest,
        *,
        authorize_creator: Callable[[User], Awaitable[None]],
    ) -> DraftReviewApproval:
        # Exact revalidation under current source, gateway and UTU authority.
        review = await self._gateway.review(session, source_agent_id, request.draft)
        # Existing agent.write and agent_access.can_create checks; raises on denial.
        await authorize_creator(review.requester)
        tenant_id = review.permit.key.tenant_id
        identity = approval_id(tenant_id, review.requester.id, request.idempotency_key)
        request_digest = request.request_digest(source_agent_id)
        record = await self._repository.get(tenant_id, identity)
        if record is not None and record.request_digest != request_digest:
            raise IdempotencyConflict("Idempotency key already used for another request")
        if not review.eligible or review.snapshot is None or review.digest is None:
            raise ReviewNotEligible("The current review is not eligible for approval")
        if review.digest != request.review_digest:
            raise ReviewStale("The draft or its authority changed since it was reviewed")
        if record is None:
            now = self._clock()
            record, _ = await self._repository.insert(
                DraftReviewApproval(
                    schema_version=1,
                    approval_id=identity,
                    tenant_id=tenant_id,
                    subject_id=review.requester.id,
                    source_agent_id=source_agent_id,
                    executor_id=review.snapshot["executor_id"],
                    request_digest=request_digest,
                    review_digest=review.digest,
                    normalized_digest=review.snapshot["normalized_digest"],
                    snapshot=review.snapshot,
                    authorization=permit_provenance(review.permit, DATA_VALIDATION_SIGNAL),
                    status="pending",
                    created_at=now,
                    expires_at=now + self._ttl,
                )
            )
        if record.status == "approved":
            return record
        if record.status == "revoked":
            raise ApprovalRevoked("The approval was revoked")
        if record.state(self._clock()) == "expired":
            raise ApprovalExpired("The pending approval expired before it took effect")
        event_id = stable_event_id(
            correlation_id=record.approval_id, event_type=APPROVED, step="decision"
        )
        # Mandatory durable evidence before the approval becomes effective.
        await self._record(record, APPROVED, event_id, record.authorization)
        await review.recheck()
        approved_at = self._clock()
        # Audit and authority checks take time: never take effect at or after expiry.
        if record.state(approved_at) == "expired":
            raise ApprovalExpired("The pending approval expired before it took effect")
        stored = await self._repository.transition(
            record,
            expected=("pending",),
            changes={
                "status": "approved",
                "approved_at": approved_at,
                "approval_event_id": event_id,
            },
        )
        if stored.status == "approved" and stored.approval_event_id == event_id:
            return stored
        if stored.status == "revoked":
            raise ApprovalRevoked("The approval was revoked")
        raise ApprovalUnavailable("Approval did not take effect; repeat the request")

    def now(self) -> datetime:
        return self._clock()

    async def owned(self, tenant_id: str, subject_id: str, approval_id: str) -> DraftReviewApproval:
        """The subject's stored approval in any state (not proof of effect)."""
        record = await self._repository.get(tenant_id, approval_id)
        if record is None or record.subject_id != subject_id:
            raise ApprovalNotFound("Approval not found")
        return record

    async def consume(self, record: DraftReviewApproval, creation_id: str) -> DraftReviewApproval:
        """Bind an effective approval to exactly one creation request, once.

        Retries of that creation see their own binding; any other request is refused.
        Callers must have verified the approval with :meth:`effective` first.
        """
        if record.consumed_by is None:
            record = await self._repository.transition(
                record,
                expected=("approved",),
                changes={"consumed_by": creation_id},
                unset=("consumed_by",),
            )
        if record.consumed_by != creation_id:
            if record.status == "revoked":
                raise ApprovalRevoked("The approval was revoked")
            raise ApprovalConsumed("The approval already authorized another creation")
        return record

    async def revoke(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        approval_id: str,
        *,
        require_unconsumed: bool = False,
    ) -> DraftReviewApproval:
        """The approving human withdraws a decision. Revocation takes effect before its
        evidence is recorded, so an audit outage can never keep an approval usable.

        ``require_unconsumed`` (draft reopening): revoke only if no creation has bound the
        approval, atomically in the same compare-and-set, else ``ApprovalConsumed``; so a
        withdrawal can never interfere with a creation already in progress.
        """
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        record = await self._repository.get(session.tenant_id, approval_id)
        # Another requester's approval is indistinguishable from a missing one.
        if (
            record is None
            or record.subject_id != session.user_id
            or record.source_agent_id != source_agent_id
        ):
            raise ApprovalNotFound("Approval not found")
        if record.status != "revoked":
            record = await self._repository.transition(
                record,
                expected=("pending", "approved"),
                changes={"status": "revoked", "revoked_at": self._clock()},
                unset=("consumed_by",) if require_unconsumed else (),
            )
        if record.status != "revoked":
            if require_unconsumed and record.consumed_by is not None:
                raise ApprovalConsumed("The approval already authorized a creation")
            raise ApprovalUnavailable("Revocation did not take effect; repeat the request")
        if record.revocation_event_id is not None:
            return record
        if record.revocation_authorization is None:
            # Attributed to the revoking session's own current UTU authority. Recorded
            # once, so a retry from any later session records identical evidence.
            try:
                provenance = await self._gateway.attribute(session, record.executor_id)
            except EventPermissionError as exc:
                raise ApprovalUnavailable(
                    "Revoked; its evidence requires current authority. Repeat the request"
                ) from exc
            record = await self._repository.transition(
                record,
                expected=("revoked",),
                changes={"revocation_authorization": provenance},
                unset=("revocation_authorization",),
            )
        if record.revocation_authorization is None:
            raise ApprovalUnavailable("Revocation evidence is not ready; repeat the request")
        event_id = stable_event_id(
            correlation_id=record.approval_id, event_type=REVOKED, step="revocation"
        )
        await self._record(record, REVOKED, event_id, record.revocation_authorization)
        return await self._repository.transition(
            record,
            expected=("revoked",),
            changes={"revocation_event_id": event_id},
            unset=("revocation_event_id",),
        )

    async def effective(
        self, tenant_id: str, subject_id: str, approval_id: str
    ) -> DraftReviewApproval:
        """The only proof a later step (G3.4) may accept that a human approved a draft.

        The approval record is authoritative; Seshat evidence is required, not
        sufficient. An approved record without exactly matching evidence, a pending
        record with evidence, and any revoked or expired approval are not effective.
        """
        record = await self._repository.get(tenant_id, approval_id)
        if record is None or record.subject_id != subject_id:
            raise ApprovalNotFound("Approval not found")
        state = record.state(self._clock())
        if state == "revoked":
            raise ApprovalRevoked("The approval was revoked")
        if state == "expired":
            raise ApprovalExpired("The approval expired")
        if state != "approved" or record.approval_event_id is None:
            raise ApprovalNotEffective("The approval has not taken effect")
        try:
            evidence = await self._audit.get(tenant_id, record.approval_event_id)
            revoked = await self._audit.get(
                tenant_id,
                stable_event_id(
                    correlation_id=record.approval_id, event_type=REVOKED, step="revocation"
                ),
            )
        except LogError as exc:
            raise ApprovalUnavailable("Approval evidence is unavailable") from exc
        expected = self._event(record, APPROVED, record.approval_event_id)
        if (
            evidence is None
            or revoked is not None
            or (evidence.event_type, evidence.outcome) != (APPROVED, "succeeded")
            or evidence.correlation_id != record.approval_id
            or evidence.subject.model_dump() != expected.subject.model_dump()
            or evidence.target.model_dump() != expected.target.model_dump()
            or evidence.metadata != expected.metadata
            or evidence.authorization != record.authorization
        ):
            raise ApprovalNotEffective("Approval evidence is missing or inconsistent")
        return record

    @staticmethod
    def _event(record: DraftReviewApproval, event_type: str, event_id: str) -> LogEvent:
        return LogEvent(
            event_id=event_id,
            event_type=event_type,
            outcome="succeeded",
            subject=LogSubject(resource_type="agent"),
            target=LogTarget(signal=DATA_VALIDATION_SIGNAL, version=DATA_VALIDATION_VERSION),
            correlation=LogCorrelation(correlation_id=record.approval_id),
            metadata={
                "event_version": 1,
                "approval_id": record.approval_id,
                "review_digest": record.review_digest,
                "normalized_digest": record.normalized_digest,
                "source_agent_id": record.source_agent_id,
            },
        )

    async def _record(
        self,
        record: DraftReviewApproval,
        event_type: str,
        event_id: str,
        authorization: AuthorizationProvenance,
    ) -> None:
        """Evidence built only from stored fields, so every retry is identical."""
        event = self._event(record, event_type, event_id)
        try:
            await self._audit.append(event, authorization=authorization)
        except LogError as exc:
            raise ApprovalUnavailable(
                "Approval evidence is unavailable; repeat the request"
            ) from exc
