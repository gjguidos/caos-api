"""Activity-context resolver for Manuscript sessions (AGENT-CTX-0.1).

Read-only: resolves a session for the verified actor through ``ManuscriptService.owned``
(another owner's, tenant's or Agent's session is indistinguishable from none) and reports
its revision, status, pinned version/fingerprint, restart predecessor and attached draft.
Never answers, guidance or artifacts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from src.domains.agentverse.activities.models import (
    ActivityReferenceUnavailable,
    ActivityUnavailable,
)
from src.domains.agentverse.activities.registry import ResolvedResource, ResourceLink
from src.domains.agentverse.manuscripts.service import ManuscriptActor, ManuscriptService
from src.domains.agentverse.manuscripts.sessions import ManuscriptError, SessionUnavailable

if TYPE_CHECKING:
    from src.domains.agentverse.activities.service import ActivityActor

MANUSCRIPT_SESSION: Final = "manuscript.session"


class ManuscriptSessionResolver:
    kind = MANUSCRIPT_SESSION

    def __init__(self, manuscripts: ManuscriptService) -> None:
        self._manuscripts = manuscripts

    async def resolve(self, actor: ActivityActor, resource_id: str) -> ResolvedResource:
        manuscript_actor = ManuscriptActor(
            actor.tenant_id, actor.subject_id, actor.principal_id, "agent", actor.requester
        )
        try:
            session, _ = await self._manuscripts.owned(manuscript_actor, resource_id)
        except SessionUnavailable as exc:
            raise ActivityUnavailable("Manuscript sessions are unavailable") from exc
        except ManuscriptError as exc:
            raise ActivityReferenceUnavailable(MANUSCRIPT_SESSION) from exc
        return ResolvedResource(
            kind=MANUSCRIPT_SESSION,
            id=session.session_id,
            revision=session.revision,
            status=session.status,
            version=session.manuscript.key(),
            fingerprint=session.manuscript.fingerprint,
            predecessor_id=session.restarted_from,
            links=()
            if session.draft_id is None
            else (ResourceLink("draft", "agent.creation_draft", session.draft_id),),
        )
