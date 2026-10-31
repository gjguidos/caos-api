"""Agent-creation activity: resolvers and the ``agent.creation_interview@1.0.0`` contract.

Owned by the drafts domain, not by any Agent: any ordinary Agent configured for it may
run it, and every reference is checked by its owning service for the verified actor.
Resolvers are read-only and return identity, revision and state only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from src.domains.agentverse.activities.models import (
    ActivityReferenceUnavailable,
    ActivityUnavailable,
)
from src.domains.agentverse.activities.owners import CONVERSATION_OWNER
from src.domains.agentverse.activities.registry import (
    ActivityContract,
    ResolvedResource,
    ResourceLink,
    ResourceSlot,
    SlotLink,
)
from src.domains.agentverse.agents.creation_drafts.models import (
    CorruptDraft,
    DraftNotFound,
    DraftUnavailable,
)
from src.domains.agentverse.agents.creation_drafts.proposal_repository import (
    MongoAgentDraftProposalRepository,
)
from src.domains.agentverse.agents.creation_drafts.proposals import ProposalUnavailable
from src.domains.agentverse.agents.creation_drafts.references import DraftReferenceReader
from src.domains.agentverse.manuscripts.activity import MANUSCRIPT_SESSION

if TYPE_CHECKING:
    from src.domains.agentverse.activities.service import ActivityActor

CREATION_DRAFT: Final = "agent.creation_draft"
DRAFT_PROPOSAL: Final = "agent.draft.proposal"
_PROPOSAL_STATES = frozenset({"pending", "deciding", "accepted", "partially_accepted", "rejected"})


class CreationDraftResolver:
    kind = CREATION_DRAFT

    def __init__(self, drafts: DraftReferenceReader) -> None:
        self._drafts = drafts

    async def resolve(self, actor: ActivityActor, resource_id: str) -> ResolvedResource:
        try:
            reference = await self._drafts.reference(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                source_agent_id=actor.principal_id,
                draft_id=resource_id,
            )
        except DraftUnavailable as exc:
            raise ActivityUnavailable("Drafts are unavailable") from exc
        except (DraftNotFound, CorruptDraft) as exc:
            raise ActivityReferenceUnavailable(CREATION_DRAFT) from exc
        return ResolvedResource(
            kind=CREATION_DRAFT,
            id=reference.draft_id,
            revision=reference.revision,
            status=reference.status,
        )


class DraftProposalResolver:
    """A proposal is the actor's only if it was made to the actor's own draft by the
    same human and source Agent (the proposal records both)."""

    kind = DRAFT_PROPOSAL

    def __init__(self, proposals: MongoAgentDraftProposalRepository) -> None:
        self._proposals = proposals

    async def resolve(self, actor: ActivityActor, resource_id: str) -> ResolvedResource:
        try:
            proposal = await self._proposals.get(actor.tenant_id, resource_id)
        except ProposalUnavailable as exc:
            raise ActivityUnavailable("Proposals are unavailable") from exc
        if proposal is None or (
            proposal.tenant_id,
            proposal.owner_subject_id,
            proposal.source_agent_id,
        ) != (actor.tenant_id, actor.subject_id, actor.principal_id):
            raise ActivityReferenceUnavailable(DRAFT_PROPOSAL)
        return ResolvedResource(
            kind=DRAFT_PROPOSAL,
            id=proposal.proposal_id,
            revision=proposal.base_revision,
            status=proposal.status,
            links=(ResourceLink("draft", CREATION_DRAFT, proposal.draft_id),),
        )


AGENT_CREATION_INTERVIEW = ActivityContract(
    id="agent.creation_interview",
    version="1.0.0",
    title="Agent creation interview",
    description="A Manuscript interview that prepares proposals for one creation draft.",
    owner_kinds=frozenset({CONVERSATION_OWNER}),
    slots=(
        ResourceSlot(
            name="manuscript_session",
            kind=MANUSCRIPT_SESSION,
            required=True,
            update="successor",
            start_statuses=frozenset({"active"}),
            update_statuses=frozenset({"active", "completed"}),
        ),
        ResourceSlot(
            name="draft",
            kind=CREATION_DRAFT,
            required=True,
            update="immutable",
            start_statuses=frozenset({"editing"}),
            update_statuses=frozenset({"editing"}),
        ),
        ResourceSlot(
            name="proposal",
            kind=DRAFT_PROPOSAL,
            required=False,
            update="replace",
            start_statuses=_PROPOSAL_STATES,
            update_statuses=_PROPOSAL_STATES,
        ),
    ),
    links=(
        SlotLink(source="manuscript_session", name="draft", target="draft"),
        SlotLink(source="proposal", name="draft", target="draft"),
    ),
)
