"""Trusted coordinator for human-owned Agent creation drafts (DRAFT-0.1).

Every operation resolves the requester afresh through the G3.3 gateway's existing
human-to-source authority and the existing creator policy; tenant and owner come
only from that verified identity. A draft write never validates, reviews, approves,
creates or activates anything, calls no LLM and records no permit or credential.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import NamedTuple

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, sha256_hex
from src.domains.agentverse.agents.creation_drafts.models import (
    LISTING_STATUSES,
    MAX_PAGE,
    AgentDraftContent,
    AgentDraftCreateRequest,
    AgentDraftForkRequest,
    AgentDraftRevision,
    AgentDraftUpdateRequest,
    DraftIdempotencyConflict,
    DraftInvalidRequest,
    DraftLocked,
    DraftNotFound,
    DraftUnavailable,
    FieldProvenance,
    ForkOrigin,
    RevisionConflict,
    decode_cursor,
    encode_cursor,
    pointer,
)
from src.domains.agentverse.agents.creation_drafts.models import (
    draft_id as draft_id_for,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationGateway

Authorize = Callable[[User], Awaitable[None]]


class Owner(NamedTuple):
    """Verified draft ownership: from the trusted requester, never a request body."""

    tenant_id: str
    subject_id: str
    # One-way UTU evidence for Seshat attribution; never a permit.
    authorization: AuthorizationProvenance


class AgentDraftService:
    def __init__(
        self,
        *,
        gateway: AgentDraftValidationGateway,
        repository: MongoAgentDraftRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._clock = clock

    async def authorize(
        self, session: VerifiedAgentSession, source_agent_id: str, authorize_creator: Authorize
    ) -> Owner:
        authority = await self._gateway.requester(session, source_agent_id)
        requester = authority.requester
        # Existing agent.write and agent_access.can_create checks; raises on denial.
        await authorize_creator(requester)
        if (
            not requester.tenant_id
            or not requester.id
            or (requester.tenant_id, requester.id) != (session.tenant_id, session.user_id)
        ):
            raise EventPermissionError("Draft requester identity mismatch")
        return Owner(requester.tenant_id, requester.id, authority.authorization)

    async def create(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        request: AgentDraftCreateRequest,
        idempotency_key: str,
        *,
        authorize_creator: Authorize,
    ) -> tuple[AgentDraftRevision, bool]:
        """Create revision 1, or replay the same request under the same key."""
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        identity = draft_id_for(
            requester.tenant_id, requester.subject_id, source_agent_id, idempotency_key
        )
        digest = request.request_digest(source_agent_id)
        now = self._clock()
        content = request.creation.data()
        first = AgentDraftRevision.build(
            draft_id=identity,
            revision=1,
            tenant_id=requester.tenant_id,
            owner_subject_id=requester.subject_id,
            source_agent_id=source_agent_id,
            status="editing",
            content=content,
            provenance={
                pointer(field): FieldProvenance(origin="user", revision=1) for field in content
            },
            creation_request_digest=digest,
            parent_digest=None,
            created_at=now,
            committed_at=now,
        )
        stored, created = await self._repository.commit(first)
        if created:
            return stored, True
        if (
            stored.owner_subject_id != requester.subject_id
            or stored.source_agent_id != source_agent_id
            or stored.creation_request_digest != digest
        ):
            raise DraftIdempotencyConflict("Idempotency key already used for another draft")
        current = await self._repository.current(requester.tenant_id, identity)
        if current is None:
            raise DraftUnavailable("Draft write outcome is unknown")
        return current, False

    async def list_drafts(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        *,
        listing: str,
        limit: int,
        cursor: str | None,
        authorize_creator: Authorize,
        reconcile: Callable[[Owner, AgentDraftRevision], Awaitable[AgentDraftRevision]]
        | None = None,
    ) -> tuple[list[AgentDraftRevision], str | None]:
        """The requester's own drafts for this source, newest first, paginated."""
        if listing not in LISTING_STATUSES or not 1 <= limit <= MAX_PAGE:
            raise DraftInvalidRequest("Invalid listing")
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        scope = {
            "drafts": [requester.tenant_id, requester.subject_id, source_agent_id],
            "listing": listing,
        }
        page, following = await self._repository.list_owned(
            requester.tenant_id,
            requester.subject_id,
            source_agent_id,
            statuses=LISTING_STATUSES[listing],
            limit=limit,
            after=decode_cursor(cursor, scope),
        )
        drafts = []
        for head in page:
            # Roll each forward (and record confirmed consumption) before returning it.
            current = await self._repository.current(requester.tenant_id, head.draft_id)
            if current is None:
                continue
            if reconcile is not None:
                current = await reconcile(requester, current)
            drafts.append(self._check_owner(current, requester, source_agent_id))
        return drafts, None if following is None else encode_cursor(following, scope)

    async def fork(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        request: AgentDraftForkRequest,
        idempotency_key: str,
        *,
        authorize_creator: Authorize,
    ) -> tuple[AgentDraftRevision, bool]:
        """A new editing draft from one of the requester's own revisions (any status).
        Same authority as creating a draft; the original is never changed."""
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        origin = self._check_owner(
            await self._repository.revision(requester.tenant_id, draft_id, request.revision),
            requester,
            source_agent_id,
        )
        identity = draft_id_for(
            requester.tenant_id, requester.subject_id, source_agent_id, idempotency_key
        )
        digest = sha256_hex(
            {
                "source_agent_id": source_agent_id,
                "fork_of": {"draft_id": origin.draft_id, "revision": origin.revision},
                "content_digest": origin.content_digest,
            }
        )
        now = self._clock()
        first = AgentDraftRevision.build(
            draft_id=identity,
            revision=1,
            tenant_id=requester.tenant_id,
            owner_subject_id=requester.subject_id,
            source_agent_id=source_agent_id,
            status="editing",
            content=origin.content,
            provenance={
                pointer(field): FieldProvenance(origin="user", revision=1)
                for field in origin.content
            },
            creation_request_digest=digest,
            parent_digest=None,
            forked_from=ForkOrigin(draft_id=origin.draft_id, revision=origin.revision),
            created_at=now,
            committed_at=now,
        )
        stored, created = await self._repository.commit(first)
        if created:
            return stored, True
        if (
            stored.owner_subject_id != requester.subject_id
            or stored.creation_request_digest != digest
        ):
            raise DraftIdempotencyConflict("Idempotency key already used for another draft")
        current = await self._repository.current(requester.tenant_id, identity)
        if current is None:
            raise DraftUnavailable("Draft write outcome is unknown")
        return current, False

    async def get(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        *,
        authorize_creator: Authorize,
    ) -> AgentDraftRevision:
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        return await self.owned(requester, source_agent_id, draft_id)

    async def revision(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        revision: int,
        *,
        authorize_creator: Authorize,
    ) -> AgentDraftRevision:
        """One immutable committed revision of the requester's own draft."""
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        record = await self._repository.revision(requester.tenant_id, draft_id, revision)
        return self._check_owner(record, requester, source_agent_id)

    async def update(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        draft_id: str,
        request: AgentDraftUpdateRequest,
        *,
        authorize_creator: Authorize,
    ) -> tuple[AgentDraftRevision, bool]:
        """Commit ``expected_revision + 1`` or fail with the current revision.

        Returns ``(current, changed)``; an update that changes nothing commits nothing.
        """
        requester = await self.authorize(session, source_agent_id, authorize_creator)
        current = await self.owned(requester, source_agent_id, draft_id)
        if current.status != "editing":
            raise DraftLocked(current.status, current.revision)
        if request.expected_revision != current.revision:
            raise RevisionConflict(current.revision)
        changes = request.changes.data()
        content = {
            field: value
            for field, value in {**current.content, **changes}.items()
            if field not in request.remove
        }
        # Structural revalidation of the merged result (closed fields, size bound).
        content = AgentDraftContent.model_validate(content).data()
        if content == current.content:
            return current, False
        revision = current.revision + 1
        provenance = {
            pointer(field): (
                FieldProvenance(origin="user", revision=revision)
                if field in changes and current.content.get(field) != value
                else current.provenance[pointer(field)]
            )
            for field, value in content.items()
        }
        record = AgentDraftRevision.build(
            draft_id=current.draft_id,
            revision=revision,
            tenant_id=current.tenant_id,
            owner_subject_id=current.owner_subject_id,
            source_agent_id=current.source_agent_id,
            status=current.status,
            content=content,
            provenance=provenance,
            creation_request_digest=current.creation_request_digest,
            parent_digest=current.revision_digest,
            forked_from=current.forked_from,
            created_at=current.created_at,
            committed_at=max(self._clock(), current.committed_at),
        )
        stored, committed = await self._repository.commit(record)
        if not committed:
            # Another update committed this revision first: never overwrite it.
            latest = await self._repository.current(current.tenant_id, current.draft_id)
            raise RevisionConflict(stored.revision if latest is None else latest.revision)
        return stored, True

    async def owned(
        self, requester: Owner, source_agent_id: str, draft_id: str
    ) -> AgentDraftRevision:
        record = await self._repository.current(requester.tenant_id, draft_id)
        return self._check_owner(record, requester, source_agent_id)

    @staticmethod
    def _check_owner(
        record: AgentDraftRevision | None, requester: Owner, source_agent_id: str
    ) -> AgentDraftRevision:
        # Another requester's, tenant's or source's draft is indistinguishable from none.
        if (
            record is None
            or record.tenant_id != requester.tenant_id
            or record.owner_subject_id != requester.subject_id
            or record.source_agent_id != source_agent_id
        ):
            raise DraftNotFound("Draft not found")
        return record
