"""Human approval of one exact, current Agent revision review (REV-3, ADR-0005).

The G3.3 approval principles, applied to revisions:

- An approval is a **human decision** about one immutable review (exact ``review_id`` and
  ``review_digest``), hence one immutable draft revision and one exact predecessor. It
  never applies a revision (REV-4), writes ``coll_agents``, grants or activates anything.
- Identity is deterministic per ``(tenant, human, review)``: every replay or concurrent
  repeat converges on one record; competing decisions are resolved by compare-and-set.
- **Approval:** a pending record, then mandatory Seshat evidence, then a fresh authority
  and currency recheck, then the ``pending → approved`` compare-and-set. A crash at any
  point leaves a non-effective record that the same request completes idempotently.
- **Withdrawal:** takes effect first (compare-and-set), evidence follows, so an audit
  outage can never keep an approval usable.
- **Effectiveness** (``effective``, for a later application step) requires the approved
  record, its exactly matching Seshat evidence, no withdrawal, no expiry, and the same
  predecessor, gateway binding and UTU policy revision as at decision time.

Cross-store limitation: the record (``coll_agent_definition_revision_approvals``) and its
evidence (Seshat ``coll_logs``) are separate documents without a transaction. The
ordering above makes every intermediate state non-effective and reconcilable by replay.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.agents.definition_revisions.drafts import PredecessorRef
from src.domains.agentverse.agents.definition_revisions.service import (
    RevisionBaselineUnavailable,
)
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

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository
    from src.domains.agentverse.agents.definition_revisions.gateway import RevisionAuthority
    from src.domains.agentverse.agents.definition_revisions.service import (
        AgentDefinitionRevisionService,
        Policy,
    )

APPROVAL_CONTRACT: Final = "caos.agent_definition_revision_approval"
APPROVALS_COLLECTION: Final = "coll_agent_definition_revision_approvals"
# How long a human decision stays usable by a later application step (as G3.3).
REVISION_APPROVAL_TTL: Final = timedelta(hours=24)
APPROVED: Final = "agent.definition_revision.approved"
WITHDRAWN: Final = "agent.definition_revision.withdrawn"
ApprovalStatus = Literal["pending", "approved", "withdrawn"]
ApprovalState = Literal["pending", "approved", "withdrawn", "expired"]


class RevisionApprovalError(Exception):
    code = "revision_approval.error"
    retryable = False


class RevisionApprovalNotFound(RevisionApprovalError):
    code = "revision_approval.not_found"


class RevisionApprovalWithdrawn(RevisionApprovalError):
    code = "revision_approval.withdrawn"


class RevisionApprovalExpired(RevisionApprovalError):
    code = "revision_approval.expired"


class RevisionApprovalNotEffective(RevisionApprovalError):
    def __init__(self, reason: str) -> None:
        super().__init__("The approval is not effective")
        self.code = f"revision_approval.{reason}"
        self.reason = reason


class RevisionApprovalConsumed(RevisionApprovalError):
    """Claimed by an application: it can no longer be withdrawn or claimed again."""

    code = "revision_approval.consumed"


class RevisionApprovalDigestMismatch(RevisionApprovalError):
    code = "revision_approval.review_digest_mismatch"


class RevisionApprovalUnavailable(RevisionApprovalError):
    code = "revision_approval.unavailable"
    retryable = True


class RevisionApprovalCorrupt(RevisionApprovalError):
    code = "revision_approval.corrupt"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def revision_approval_id(tenant_id: str, subject_id: str, review_id: str) -> str:
    """One decision per human per review: replays and concurrent repeats converge."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-definition-revision-approval:"
            + sha256_hex({"tenant": tenant_id, "subject": subject_id, "review": review_id}),
        )
    )


class RevisionApprovalRequest(_Frozen):
    """Explicit approval of the exact review the human holds."""

    contract: Literal["caos.agent_definition_revision_approval_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    review_id: str = Field(min_length=1, max_length=64)
    review_digest: Digest
    decision: Literal["approve"]
    confirmed: Literal[True]

    def request_digest(self, agent_id: str) -> str:
        return sha256_hex(
            {
                "agent_id": agent_id,
                "review_id": self.review_id,
                "review_digest": self.review_digest,
                "decision": self.decision,
            }
        )


class RevisionWithdrawalRequest(_Frozen):
    contract: Literal["caos.agent_definition_revision_withdrawal_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    confirmed: Literal[True]


class RevisionApproval(BaseModel):
    """The complete trusted record. ``public`` is the human-safe projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_definition_revision_approval"] = APPROVAL_CONTRACT
    schema_version: Literal[1] = 1
    approval_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    executor_id: str = Field(min_length=1, max_length=128)
    review_id: str = Field(min_length=1, max_length=64)
    review_digest: Digest
    draft_id: str = Field(min_length=1, max_length=64)
    draft_revision: int = Field(ge=1)
    candidate_digest: Digest
    candidate_definition_digest: Digest
    predecessor: PredecessorRef
    gateway_fingerprint: Digest
    utu_policy_revision: str = Field(min_length=1, max_length=256)
    request_digest: Digest
    # Decision-time provenance, reused so a recovered decision records identical evidence.
    authorization: AuthorizationProvenance
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    approval_event_id: str | None = None
    withdrawn_at: datetime | None = None
    withdrawal_authorization: AuthorizationProvenance | None = None
    withdrawal_event_id: str | None = None
    # REV-4: the single application (exact ``application_id``) that consumed this
    # approval, and when. Set once, only on an approved, unexpired record (REV-5: the
    # consumption must be committed before expiry); afterwards withdrawal is refused.
    consumed_by: str | None = Field(default=None, min_length=1, max_length=64)
    consumed_at: datetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.approval_id != revision_approval_id(
            self.tenant_id, self.subject_id, self.review_id
        ):
            raise ValueError("Approval identity mismatch")
        if (self.authorization.tenant_id, self.authorization.subject_id) != (
            self.tenant_id,
            self.subject_id,
        ) or self.authorization.origin != "session":
            raise ValueError("Approval provenance must be the requester's human session")
        if (self.approved_at is None) != (self.approval_event_id is None):
            raise ValueError("Approval time and evidence are recorded together")
        if self.status == "approved" and self.approved_at is None:
            raise ValueError("An approved record carries its evidence")
        if self.status == "pending" and (
            self.approved_at is not None or self.withdrawn_at is not None
        ):
            raise ValueError("A pending approval has no decision or withdrawal")
        if (self.status == "withdrawn") != (self.withdrawn_at is not None):
            raise ValueError("Only a withdrawn approval has a withdrawal time")
        withdrawal = self.withdrawal_authorization
        if withdrawal is not None and (
            self.status != "withdrawn"
            or (withdrawal.tenant_id, withdrawal.subject_id) != (self.tenant_id, self.subject_id)
        ):
            raise ValueError("Withdrawal provenance must be the requester's")
        if self.withdrawal_event_id is not None and withdrawal is None:
            raise ValueError("Withdrawal evidence requires its provenance")
        if self.expires_at <= self.created_at:
            raise ValueError("An approval expires after it is requested")
        if self.approved_at is not None and self.approved_at >= self.expires_at:
            raise ValueError("An approval takes effect only before it expires")
        if self.consumed_by is not None and self.approved_at is None:
            raise ValueError("Only an approval that took effect can be consumed")
        if (self.consumed_by is None) != (self.consumed_at is None):
            raise ValueError("Consumption and its time are recorded together")
        if self.consumed_at is not None and self.consumed_at >= self.expires_at:
            raise ValueError("An approval can only be consumed before it expires")
        return self

    def state(self, now: datetime) -> ApprovalState:
        if self.status == "withdrawn":
            return "withdrawn"
        if now >= self.expires_at:
            return "expired"
        return self.status

    def public(self, now: datetime) -> dict[str, Any]:
        """Human-safe: no provenance, policy revision, executor, fingerprints or
        definition digests; the owner sees their own decision and its references."""
        return {
            "contract": self.contract,
            "schema_version": self.schema_version,
            "approval_id": self.approval_id,
            "agent_id": self.agent_id,
            "review_id": self.review_id,
            "review_digest": self.review_digest,
            "draft": {"draft_id": self.draft_id, "revision": self.draft_revision},
            "predecessor": {
                "revision": self.predecessor.revision,
                "materialized": self.predecessor.materialized,
            },
            "state": self.state(now),
            "approved_at": None if self.approved_at is None else self.approved_at.isoformat(),
            "withdrawn_at": None if self.withdrawn_at is None else self.withdrawn_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            # An approval never applies or activates a revision (REV-4, Khepri).
            "authorizes": {"application": False, "activation": False},
        }


def _dump(record: BaseModel) -> dict[str, Any]:
    return json.loads(record.model_dump_json())


class MongoRevisionApprovalRepository:
    """``_id = approval_id``; insert once, then status-guarded compare-and-set only."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection: str = APPROVALS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection)
        except PyMongoError as exc:
            raise RevisionApprovalUnavailable("Approval store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise RevisionApprovalUnavailable("Approvals require acknowledged writes")
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    async def get(self, tenant_id: str, approval_id: str) -> RevisionApproval | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": approval_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise RevisionApprovalUnavailable("Approval store is unavailable") from exc
        if document is None:
            return None
        try:
            data = dict(document)
            data.pop("_id")
            return RevisionApproval.model_validate_json(json.dumps(data))
        except (ValueError, TypeError) as exc:
            raise RevisionApprovalCorrupt("Invalid stored approval") from exc

    async def insert(self, record: RevisionApproval) -> tuple[RevisionApproval, bool]:
        collection = await self._collection()
        try:
            await collection.insert_one({"_id": record.approval_id, **_dump(record)})
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise RevisionApprovalUnavailable("Approval write outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.approval_id)
        if stored is None:
            raise RevisionApprovalUnavailable("Approval write outcome is unknown")
        return stored, False

    async def transition(
        self,
        record: RevisionApproval,
        *,
        expected: tuple[ApprovalStatus, ...],
        changes: dict[str, Any],
        unset: tuple[str, ...] = (),
    ) -> RevisionApproval:
        """Apply ``changes`` only while the status is expected and ``unset`` fields are
        still empty; returns the stored record either way."""
        updated = RevisionApproval.model_validate({**record.model_dump(), **changes})
        values = {key: _dump(updated)[key] for key in changes}
        collection = await self._collection()
        try:
            await collection.update_one(
                {
                    "_id": record.approval_id,
                    "tenant_id": record.tenant_id,
                    "status": {"$in": list(expected)},
                    **{field: None for field in unset},
                },
                {"$set": values},
            )
        except PyMongoError as exc:
            raise RevisionApprovalUnavailable("Approval transition outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.approval_id)
        if stored is None:
            raise RevisionApprovalCorrupt("Approval disappeared during a transition")
        return stored


class RevisionApprovalService:
    """Human decisions on current revision reviews; never applies a revision."""

    def __init__(
        self,
        *,
        revisions: AgentDefinitionRevisionService,
        repository: MongoRevisionApprovalRepository,
        audit: AuditSink,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = REVISION_APPROVAL_TTL,
    ) -> None:
        self._revisions = revisions
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._ttl = ttl

    def now(self) -> datetime:
        return self._clock()

    async def approve(
        self,
        session: Any,
        agent_id: str,
        request: RevisionApprovalRequest,
        *,
        policy: Policy | None = None,
    ) -> RevisionApproval:
        # Fresh human authority, route policy, target ownership and review currency.
        auth = await self._revisions.authorize(session, policy)
        review = await self._revisions.current_review(auth, agent_id, request.review_id)
        if review.review_digest != request.review_digest:
            raise RevisionApprovalDigestMismatch("The review digest differs")
        identity = revision_approval_id(auth.tenant_id, auth.subject_id, review.review_id)
        request_digest = request.request_digest(agent_id)
        record = await self._repository.get(auth.tenant_id, identity)
        if record is None:
            snapshot = review.snapshot
            now = self._clock()
            record, _ = await self._repository.insert(
                RevisionApproval(
                    approval_id=identity,
                    tenant_id=auth.tenant_id,
                    subject_id=auth.subject_id,
                    agent_id=agent_id,
                    executor_id=review.executor_id,
                    review_id=review.review_id,
                    review_digest=request.review_digest,
                    draft_id=review.draft_id,
                    draft_revision=review.draft_revision,
                    candidate_digest=snapshot["draft"]["candidate_digest"],
                    candidate_definition_digest=snapshot["candidate_definition_digest"],
                    predecessor=PredecessorRef.model_validate(snapshot["predecessor"]),
                    gateway_fingerprint=snapshot["gateway_fingerprint"],
                    utu_policy_revision=snapshot["policy"]["utu_policy_revision"],
                    request_digest=request_digest,
                    authorization=auth.authorization,
                    status="pending",
                    created_at=now,
                    expires_at=now + self._ttl,
                )
            )
        if record.request_digest != request_digest:
            raise RevisionApprovalDigestMismatch("The approval was requested differently")
        if record.status == "approved":
            return record
        if record.status == "withdrawn":
            raise RevisionApprovalWithdrawn("The approval was withdrawn")
        if record.state(self._clock()) == "expired":
            raise RevisionApprovalExpired("The pending approval expired")
        event_id = stable_event_id(
            correlation_id=record.approval_id, event_type=APPROVED, step="decision"
        )
        # Mandatory durable evidence before the decision can take effect.
        await self._record(record, APPROVED, event_id, record.authorization)
        # Fresh authority and currency again: a revoked session, changed gateway or
        # policy, newer draft or changed predecessor leaves the record non-effective.
        await self._revisions.recheck(auth)
        await self._revisions.current_review(auth, agent_id, review.review_id)
        approved_at = self._clock()
        if record.state(approved_at) == "expired":
            raise RevisionApprovalExpired("The pending approval expired")
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
        if stored.status == "withdrawn":
            raise RevisionApprovalWithdrawn("The approval was withdrawn")
        raise RevisionApprovalUnavailable("Approval did not take effect; repeat the request")

    async def withdraw(
        self,
        session: Any,
        agent_id: str,
        approval_id: str,
        request: RevisionWithdrawalRequest,
        *,
        policy: Policy | None = None,
    ) -> RevisionApproval:
        """The approving human withdraws a pending or approved, unconsumed decision.
        It takes effect before its evidence is recorded."""
        auth = await self._revisions.authorize(session, policy)
        await self._revisions.require_target(auth, agent_id)
        record = await self._owned(auth, agent_id, approval_id)
        if record.consumed_by is not None:
            # Claimed by an application (REV-4): the claim is the point of no withdrawal.
            raise RevisionApprovalConsumed("The approval was already claimed for application")
        if record.status != "withdrawn":
            record = await self._repository.transition(
                record,
                expected=("pending", "approved"),
                changes={
                    "status": "withdrawn",
                    "withdrawn_at": self._clock(),
                    "withdrawal_authorization": auth.authorization,
                },
                unset=("consumed_by",),
            )
        if record.status != "withdrawn" and record.consumed_by is not None:
            raise RevisionApprovalConsumed("The approval was claimed for application first")
        if record.status != "withdrawn" or record.withdrawal_authorization is None:
            raise RevisionApprovalUnavailable("Withdrawal did not take effect; repeat it")
        if record.withdrawal_event_id is not None:
            return record
        event_id = stable_event_id(
            correlation_id=record.approval_id, event_type=WITHDRAWN, step="withdrawal"
        )
        await self._record(record, WITHDRAWN, event_id, record.withdrawal_authorization)
        return await self._repository.transition(
            record,
            expected=("withdrawn",),
            changes={"withdrawal_event_id": event_id},
            unset=("withdrawal_event_id",),
        )

    async def status(
        self, session: Any, agent_id: str, approval_id: str, *, policy: Policy | None = None
    ) -> dict[str, Any]:
        """The human-safe record and whether it is currently effective (read-only)."""
        auth = await self._revisions.authorize(session, policy)
        await self._revisions.require_target(auth, agent_id)
        record = await self._owned(auth, agent_id, approval_id)
        try:
            await self._effective(auth, record)
            effective, reason = True, None
        except RevisionApprovalError as exc:
            effective, reason = False, exc.code
        return {**record.public(self._clock()), "effective": effective, "reason": reason}

    async def effective(
        self, session: Any, agent_id: str, approval_id: str, *, policy: Policy | None = None
    ) -> RevisionApproval:
        """The only proof a later application step (REV-4) may accept."""
        auth = await self._revisions.authorize(session, policy)
        record = await self._owned(auth, agent_id, approval_id)
        await self._effective(auth, record)
        return record

    async def owned(
        self, auth: RevisionAuthority, agent_id: str, approval_id: str
    ) -> RevisionApproval:
        return await self._owned(auth, agent_id, approval_id)

    async def check_effective(self, auth: RevisionAuthority, record: RevisionApproval) -> None:
        """Effectiveness under the given (fresh) authority; raises if not effective."""
        await self._effective(auth, record)

    async def consume(self, record: RevisionApproval, application_id: str) -> RevisionApproval:
        """Bind an effective approval to exactly one application, once (the claim).

        A withdrawal committed first wins (the compare-and-set requires ``approved``);
        afterwards withdrawal is refused. The consumption time is written in the same
        compare-and-set and must precede expiry, so an approval that expires between the
        effectiveness check and consumption is never consumed. Retries of the same
        application see their binding. Callers must have verified effectiveness first.
        """
        if record.consumed_by is None:
            now = self._clock()
            if record.state(now) == "expired":
                raise RevisionApprovalExpired("The approval expired before the claim")
            try:
                record = await self._repository.transition(
                    record,
                    expected=("approved",),
                    changes={"consumed_by": application_id, "consumed_at": now},
                    unset=("consumed_by",),
                )
            except ValueError:
                raise RevisionApprovalExpired("The approval expired before the claim") from None
        if record.status == "withdrawn":
            raise RevisionApprovalWithdrawn("The approval was withdrawn before the claim")
        if record.consumed_by != application_id:
            raise RevisionApprovalConsumed("The approval was claimed by another application")
        return record

    async def verify_claimed(
        self, tenant_id: str, approval_id: str, application_id: str
    ) -> RevisionApproval:
        """For the Store write boundary (authority is the scoped Genesis permit there):
        the approval is approved, consumed by exactly this claim, never withdrawn, and
        its approval evidence matches. Expiry no longer applies after the claim."""
        record = await self._repository.get(tenant_id, approval_id)
        if (
            record is None
            or record.status != "approved"
            or record.consumed_by != application_id
            or record.approval_event_id is None
        ):
            raise RevisionApprovalNotEffective("not_claimed")
        try:
            evidence = await self._audit.get(tenant_id, record.approval_event_id)
        except LogError as exc:
            raise RevisionApprovalUnavailable("Approval evidence is unavailable") from exc
        expected = self._event(record, APPROVED, record.approval_event_id)
        if (
            evidence is None
            or evidence.metadata != expected.metadata
            or evidence.authorization != record.authorization
        ):
            raise RevisionApprovalNotEffective("evidence_mismatch")
        return record

    async def _owned(
        self, auth: RevisionAuthority, agent_id: str, approval_id: str
    ) -> RevisionApproval:
        record = await self._repository.get(auth.tenant_id, approval_id)
        # Another human's or Agent's approval is indistinguishable from a missing one.
        if record is None or (record.subject_id, record.agent_id) != (auth.subject_id, agent_id):
            raise RevisionApprovalNotFound("Approval not found")
        return record

    async def _effective(self, auth: RevisionAuthority, record: RevisionApproval) -> None:
        state = record.state(self._clock())
        if state == "withdrawn":
            raise RevisionApprovalWithdrawn("The approval was withdrawn")
        if state == "expired":
            raise RevisionApprovalExpired("The approval expired")
        if state != "approved" or record.approval_event_id is None:
            raise RevisionApprovalNotEffective("pending")
        try:
            evidence = await self._audit.get(record.tenant_id, record.approval_event_id)
            withdrawal = await self._audit.get(
                record.tenant_id,
                stable_event_id(
                    correlation_id=record.approval_id, event_type=WITHDRAWN, step="withdrawal"
                ),
            )
        except LogError as exc:
            raise RevisionApprovalUnavailable("Approval evidence is unavailable") from exc
        expected = self._event(record, APPROVED, record.approval_event_id)
        if (
            evidence is None
            or withdrawal is not None
            or (evidence.event_type, evidence.outcome) != (APPROVED, "succeeded")
            or evidence.correlation_id != record.approval_id
            or evidence.subject.model_dump() != expected.subject.model_dump()
            or evidence.metadata != expected.metadata
            or evidence.authorization != record.authorization
        ):
            raise RevisionApprovalNotEffective("evidence_mismatch")
        if (
            auth.binding.fingerprint() != record.gateway_fingerprint
            or auth.binding.executor_id != record.executor_id
            or auth.permit.versions[0] != record.utu_policy_revision
        ):
            raise RevisionApprovalNotEffective("stale_authority")
        try:
            predecessor = await self._revisions.current_predecessor(auth, record.agent_id)
        except RevisionBaselineUnavailable:
            raise RevisionApprovalNotEffective("stale_predecessor") from None
        if predecessor != record.predecessor:
            raise RevisionApprovalNotEffective("stale_predecessor")

    @staticmethod
    def _event(record: RevisionApproval, event_type: EventType, event_id: str) -> LogEvent:
        """Built only from stored fields, so every retry records identical evidence."""
        return LogEvent(
            event_id=event_id,
            event_type=event_type,
            outcome="succeeded",
            subject=LogSubject(resource_type="agent", resource_id=record.agent_id),
            target=LogTarget(signal=DATA_VALIDATION_SIGNAL, version=DATA_VALIDATION_VERSION),
            correlation=LogCorrelation(correlation_id=record.approval_id),
            metadata={
                "event_version": 1,
                "approval_id": record.approval_id,
                "agent_id": record.agent_id,
                "executor_id": record.executor_id,
                "review_id": record.review_id,
                "review_digest": record.review_digest,
                "draft_id": record.draft_id,
                "draft_revision": record.draft_revision,
                "candidate_definition_digest": record.candidate_definition_digest,
                "predecessor_revision": record.predecessor.revision,
                "predecessor_revision_digest": record.predecessor.revision_digest,
                "gateway_fingerprint": record.gateway_fingerprint,
                "utu_policy_revision": record.utu_policy_revision,
            },
        )

    async def _record(
        self,
        record: RevisionApproval,
        event_type: EventType,
        event_id: str,
        authorization: AuthorizationProvenance,
    ) -> None:
        try:
            await self._audit.append(
                self._event(record, event_type, event_id), authorization=authorization
            )
        except LogError as exc:
            raise RevisionApprovalUnavailable(
                "Approval evidence is unavailable; repeat the request"
            ) from exc
