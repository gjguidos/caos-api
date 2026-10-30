"""Draft review, approval binding and lifecycle (DRAFT-0.2B).

Composes the existing governance without a second engine:

* **Review** of an exact committed revision through the unchanged G3.3 gateway; the
  review digest is the same as for the same content sent as a body.
* **Approval** of the reviewed revision through the unchanged G3.3
  ``DraftReviewApprovalService.approve`` (current authority, dependency fingerprints,
  Seshat evidence). The draft is then frozen by a content-unchanged ``approved``
  revision through the revision CAS; if the draft moved meanwhile, the just-made
  approval is withdrawn (only while unconsumed) and the human sees a revision conflict.
* **Reopen** returns a frozen draft to editing only when no creation is using its
  approval: an unconsumed approval is revoked atomically (``require_unconsumed``), a
  creation that failed has spent it; an accepted or provisioning creation refuses.
* **Consumption** is recorded once G3.4 *confirms* the creation (``completed``), as an
  idempotent, content-unchanged ``consumed`` revision; consumed drafts are read-only.

Lifecycle revisions record no Seshat event: approval, revocation and creation evidence
already exist in G3.3/G3.4. Nothing here grants, creates or activates.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.base.operations.v2.admission import Digest
from src.domains.agentverse.agents.creation_drafts.models import (
    AgentDraftRevision,
    DraftCreationInProgress,
    DraftLocked,
    RevisionCause,
    RevisionConflict,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_drafts.service import (
    AgentDraftService,
    Authorize,
    Owner,
)
from src.domains.agentverse.agents.creation_review.creation import MongoAgentCreationRepository
from src.domains.agentverse.agents.creation_review.models import (
    ApprovalConsumed,
    ApprovalNotFound,
    DraftReviewApproval,
    IdempotencyKey,
    ReviewApprovalRequest,
    approval_id,
)
from src.domains.agentverse.agents.creation_review.service import DraftReviewApprovalService
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.security.validation_gateway import (
    AgentDraftValidationGateway,
    AgentDraftValidationRequest,
)

_ATTEMPTS = 3


class DraftApprovalRequest(BaseModel):
    """Explicit human approval of the exact reviewed revision; never creation authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_approval"]
    schema_version: StrictInt = Field(ge=1, le=1)
    revision: StrictInt = Field(ge=1)
    review_digest: Digest
    decision: Literal["approve"]
    confirmed: Literal[True]
    idempotency_key: IdempotencyKey


class DraftReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_reopen"]
    schema_version: StrictInt = Field(ge=1, le=1)
    expected_revision: StrictInt = Field(ge=1)


class AgentDraftLifecycleService:
    def __init__(
        self,
        *,
        drafts: AgentDraftService,
        revisions: MongoAgentDraftRepository,
        gateway: AgentDraftValidationGateway,
        approvals: DraftReviewApprovalService,
        creations: MongoAgentCreationRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._drafts = drafts
        self._revisions = revisions
        self._gateway = gateway
        self._approvals = approvals
        self._creations = creations
        self._clock = clock

    # --- Reads -------------------------------------------------------------------------------

    async def get(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        *,
        authorize_creator: Authorize,
    ) -> AgentDraftRevision:
        """The current draft, with a confirmed creation recorded as consumption."""
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        return await self.reconcile(
            owner, await self._drafts.owned(owner, source_agent_id, draft_id)
        )

    async def reconcile(self, owner: Owner, draft: AgentDraftRevision) -> AgentDraftRevision:
        """Record consumption once G3.4 confirms the creation; idempotent and
        content-unchanged. Anything short of a confirmed creation changes nothing."""
        for _ in range(_ATTEMPTS):
            if draft.status != "approved" or draft.cause is None:
                return draft
            cause = draft.cause
            assert cause.approval_id is not None
            try:
                approval = await self._approvals.owned(
                    owner.tenant_id, owner.subject_id, cause.approval_id
                )
            except ApprovalNotFound:
                return draft
            if approval.consumed_by is None:
                return draft
            creation = await self._creations.get(owner.tenant_id, approval.consumed_by)
            if (
                creation is None
                or creation.state != "completed"
                or creation.agent_id is None
                or creation.approval_id != approval.approval_id
            ):
                return draft
            record = self._lifecycle(
                draft,
                "consumed",
                RevisionCause(
                    kind="consumption",
                    approval_id=approval.approval_id,
                    review_digest=cause.review_digest,
                    reviewed_revision=cause.reviewed_revision,
                    creation_id=creation.creation_id,
                    agent_id=creation.agent_id,
                ),
            )
            stored, committed = await self._revisions.commit(record)
            if committed:
                return stored
            draft = await self._current(draft)  # Another reader recorded it first.
        return draft

    # --- Review and approval -------------------------------------------------------------------

    async def review(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        revision: int,
        *,
        authorize_creator: Authorize,
    ) -> dict[str, Any]:
        """G3.3 review of one exact committed revision (server-read content)."""
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        record = await self._revisions.revision(owner.tenant_id, draft_id, revision)
        record = self._drafts._check_owner(record, owner, source_agent_id)
        request = AgentDraftValidationRequest.model_validate(record.creation_draft())
        envelope = await self._gateway.validate(session, source_agent_id, request)
        return {
            **envelope,
            "draft_ref": {
                "draft_id": record.draft_id,
                "revision": record.revision,
                "content_digest": record.content_digest,
            },
        }

    async def approve(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        request: DraftApprovalRequest,
        *,
        authorize_creator: Authorize,
    ) -> tuple[DraftReviewApproval, AgentDraftRevision]:
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        current = await self.reconcile(
            owner, await self._drafts.owned(owner, source_agent_id, draft_id)
        )
        identity = approval_id(owner.tenant_id, owner.subject_id, request.idempotency_key)
        if current.status != "editing":
            if current.cause is not None and current.cause.approval_id == identity:
                # Replay of the decision that froze this draft.
                approval = await self._approvals.owned(owner.tenant_id, owner.subject_id, identity)
                return approval, current
            raise DraftLocked(current.status, current.revision)
        if request.revision != current.revision:
            raise RevisionConflict(current.revision)
        # The unchanged G3.3 approval: re-review under current authority and dependency
        # fingerprints, exact digest match, durable idempotency and Seshat evidence.
        approval = await self._approvals.approve(
            session,
            source_agent_id,
            ReviewApprovalRequest(
                contract="caos.agent_creation_review_approval",
                schema_version=1,
                draft=AgentDraftValidationRequest.model_validate(current.creation_draft()),
                review_digest=request.review_digest,
                decision="approve",
                confirmed=True,
                idempotency_key=request.idempotency_key,
            ),
            authorize_creator=authorize_creator,
        )
        record = self._lifecycle(
            current,
            "approved",
            RevisionCause(
                kind="approval",
                approval_id=approval.approval_id,
                review_digest=approval.review_digest,
                reviewed_revision=current.revision,
            ),
        )
        stored, committed = await self._revisions.commit(record)
        if committed or (
            stored.cause is not None and stored.cause.approval_id == approval.approval_id
        ):
            return approval, stored
        # The draft changed after the review: the approval must not outlive its revision.
        try:
            await self._approvals.revoke(
                session, source_agent_id, approval.approval_id, require_unconsumed=True
            )
        except ApprovalConsumed:
            pass  # Already bound to a creation of exactly the approved content.
        raise RevisionConflict((await self._current(current)).revision)

    async def reopen(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        request: DraftReopenRequest,
        *,
        authorize_creator: Authorize,
    ) -> AgentDraftRevision:
        """Return a frozen draft to editing; a new review and approval are then needed."""
        owner = await self._drafts.authorize(session, source_agent_id, authorize_creator)
        current = await self.reconcile(
            owner, await self._drafts.owned(owner, source_agent_id, draft_id)
        )
        if current.status == "editing":
            return current
        if current.status == "consumed":
            raise DraftLocked(current.status, current.revision)
        if request.expected_revision != current.revision:
            raise RevisionConflict(current.revision)
        cause = current.cause
        assert cause is not None and cause.approval_id is not None
        try:
            approval = await self._approvals.owned(
                owner.tenant_id, owner.subject_id, cause.approval_id
            )
        except ApprovalNotFound:
            approval = None
        if approval is not None and approval.consumed_by is not None:
            creation = await self._creations.get(owner.tenant_id, approval.consumed_by)
            if creation is None or creation.state != "failed":
                # Accepted, provisioning, or completed (recorded as consumption on read).
                raise DraftCreationInProgress("A creation is using this draft's approval")
        elif approval is not None and approval.state(self._clock()) in ("pending", "approved"):
            try:
                await self._approvals.revoke(
                    session, source_agent_id, approval.approval_id, require_unconsumed=True
                )
            except ApprovalConsumed as exc:
                raise DraftCreationInProgress("A creation is using this draft's approval") from exc
        record = self._lifecycle(
            current, "editing", RevisionCause(kind="reopen", approval_id=cause.approval_id)
        )
        stored, committed = await self._revisions.commit(record)
        if not committed and stored.status != "editing":
            raise RevisionConflict((await self._current(current)).revision)
        return stored if committed else await self._current(current)

    # --- Internals ---------------------------------------------------------------------------

    async def _current(self, draft: AgentDraftRevision) -> AgentDraftRevision:
        current = await self._revisions.current(draft.tenant_id, draft.draft_id)
        if current is None:
            raise RevisionConflict(draft.revision)
        return current

    def _lifecycle(
        self, draft: AgentDraftRevision, status: str, cause: RevisionCause
    ) -> AgentDraftRevision:
        """A content-unchanged revision: same content, digest and field provenance."""
        return AgentDraftRevision.build(
            draft_id=draft.draft_id,
            revision=draft.revision + 1,
            tenant_id=draft.tenant_id,
            owner_subject_id=draft.owner_subject_id,
            source_agent_id=draft.source_agent_id,
            status=status,
            content=draft.content,
            provenance=draft.provenance,
            creation_request_digest=draft.creation_request_digest,
            parent_digest=draft.revision_digest,
            forked_from=draft.forked_from,
            created_at=draft.created_at,
            committed_at=max(self._clock(), draft.committed_at),
            cause=cause,
        )
