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

from src.domains.agentverse.agents.creation_drafts.models import DraftStatus
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_drafts.service import owned_record


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
