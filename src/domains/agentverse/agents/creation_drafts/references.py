"""Read-only draft references for other domains (MANUSCRIPT-0.1C).

Resolves a draft ID for a trusted ``(tenant, owner subject, source Agent)`` and returns
only its identity, revision and status: never content, provenance or a write handle.
Ownership uses the draft service's own rule, so a missing, foreign-tenant, other-human or
other-source draft is indistinguishable (``DraftNotFound``). Reading uses the draft
repository's normal ``current`` path, which may idempotently move the derived head
projection to an already committed revision (as every draft read does); it never creates
a revision or changes content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.models import DraftStatus
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_drafts.service import owned_record
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority


@dataclass(frozen=True)
class DraftReference:
    draft_id: str
    revision: int
    status: DraftStatus


class DraftReferenceReader:
    def __init__(self, repository: MongoAgentDraftRepository) -> None:
        self._repository = repository

    async def reference(
        self, *, tenant_id: str, subject_id: str, source_agent_id: str, draft_id: str
    ) -> DraftReference:
        record = owned_record(
            await self._repository.current(tenant_id, draft_id),
            tenant_id,
            subject_id,
            source_agent_id,
        )
        return DraftReference(record.draft_id, record.revision, record.status)


@dataclass(frozen=True)
class DraftContent:
    """The current committed revision's content: no provenance, history or write handle."""

    draft_id: str
    revision: int
    status: DraftStatus
    content: dict[str, Any]


class DraftContentReader:
    """Read-only content of a draft, only within an authorized proposal operation (COORD-0.3).

    Not a capability, route or Tool: a trusted in-process port held only by the creation
    interview coordinator, which compares a completed Manuscript artifact with the draft
    so that its ``agent.draft.propose`` submission names exactly the changed fields.

    The caller presents the acting Agent's principal attenuated to the proposal scope
    (``agent.draft.propose`` only). The reader independently requires a session-origin
    permit, re-verifies it against current UTU authority (permit, policy versions and the
    ``agent.draft.propose`` grant) and takes tenant, human and source Agent from the
    permit alone, never from the caller. Ownership is the draft service's own rule (the
    same read ``propose`` performs), so another tenant's, human's or source Agent's draft
    is indistinguishable from none. It never writes, decides or logs content.
    """

    def __init__(self, repository: MongoAgentDraftRepository, authority: UTUAuthority) -> None:
        self._repository = repository
        self._authority = authority

    async def content(self, principal: AuthorizedAgentPrincipal, draft_id: str) -> DraftContent:
        from src.domains.agentverse.agents.creation_drafts.capability import PROPOSE_SCOPE
        from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSE_SIGNAL

        permit = principal.permit if isinstance(principal, AuthorizedAgentPrincipal) else None
        if (
            not isinstance(permit, ExecutionPermit)
            or permit.origin != "session"
            or permit.scope != PROPOSE_SCOPE
            or (principal.agent_id, principal.tenant_id)
            != (permit.key.agent_id, permit.key.tenant_id)
        ):
            raise EventPermissionError("Draft content requires the proposal scope")
        await self._authority.check_permit(permit)
        current = await self._authority.resolve_agent_principal(permit.key)
        if (
            current.permit.versions != permit.versions
            or PROPOSE_SIGNAL not in self._authority.grants_for(current).invoke
        ):
            raise EventPermissionError("Proposal authority changed")
        key = permit.key
        record = owned_record(
            await self._repository.current(key.tenant_id, draft_id),
            key.tenant_id,
            key.user_id,
            key.agent_id,
        )
        return DraftContent(record.draft_id, record.revision, record.status, dict(record.content))
