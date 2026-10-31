"""Owner kinds: the resource an activity context is anchored to (AGENT-CTX-0.1).

``conversation.v2`` is the only owner kind. Access is the human's current V2
conversation membership plus access to the acting Agent (UTU), and the Agent's existing
conversational turn grant. A write's owner attempt is the calling turn's CONV-REC-0.1
lease: live, this conversation and human. No other authority is introduced.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final, Protocol

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.activities.models import (
    ActivityNotFound,
    ActivityStaleOwner,
    ActivityUnavailable,
    Causation,
)
from src.domains.agentverse.repositories.conversation_v2 import (
    ConversationRepositoryV2,
    TurnLease,
)
from src.domains.agentverse.security.utu import UTUAuthority

if TYPE_CHECKING:
    from src.domains.agentverse.activities.service import ActivityActor

CONVERSATION_OWNER: Final = "conversation.v2"
# The conversational turn capability under which an Agent maintains its contexts.
TURN_SIGNAL: Final = "chat.response"
TURN_VERSION: Final = "1.0.0"


class OwnerResolver(Protocol):
    kind: str
    audit_signal: str
    audit_version: str

    async def authorize(self, actor: ActivityActor, owner_id: str) -> None:
        """Fresh access to the owner; denial is ``ActivityNotFound``."""

    async def causation(
        self, actor: ActivityActor, owner_id: str, attempt: Any, operation_id: str
    ) -> Causation:
        """The calling owner attempt, verified live; else ``ActivityStaleOwner``."""

    async def permits_activity(self, actor: ActivityActor, owner_id: str, activity: str) -> bool:
        """Consent for a new generation (start or switch) of ``activity``: only an explicit
        declaration consents. It grants nothing; ``authorize`` still applies."""


class ConversationOwner:
    kind = CONVERSATION_OWNER
    audit_signal = TURN_SIGNAL
    audit_version = TURN_VERSION

    def __init__(
        self,
        *,
        conversations: ConversationRepositoryV2,
        authority: UTUAuthority,
        activities: Callable[[str, str], Awaitable[frozenset[str]]] | None = None,
    ) -> None:
        self._conversations = conversations
        self._authority = authority
        # REV-5: the Agent's currently declared activity contracts. Injected so this
        # package never depends on the Conversation Tool. MIG-FIX-0.1: an unwired source
        # (or a non-set answer) declares nothing, so no new generation is consented.
        self._activities = activities

    async def permits_activity(self, actor: ActivityActor, owner_id: str, activity: str) -> bool:
        if self._activities is None:
            return False
        declared = await self._activities(actor.tenant_id, actor.principal_id)
        return isinstance(declared, frozenset) and activity in declared

    async def authorize(self, actor: ActivityActor, owner_id: str) -> None:
        if TURN_SIGNAL not in actor.grants:
            raise ActivityNotFound("Activity context not found")
        try:
            conversation = await self._conversations.get(actor.tenant_id, owner_id)
            await self._authority.authorize_conversation(
                session_id=actor.session_id,
                conversation=conversation,
                agent_ids=(actor.principal_id,),
            )
        except EventPermissionError as exc:
            raise ActivityNotFound("Activity context not found") from exc

    async def causation(
        self, actor: ActivityActor, owner_id: str, attempt: Any, operation_id: str
    ) -> Causation:
        if not isinstance(attempt, TurnLease) or (attempt.tenant_id, attempt.conversation_id) != (
            actor.tenant_id,
            owner_id,
        ):
            raise ActivityStaleOwner("The turn does not belong to this conversation")
        user, event_id = json.loads(attempt.identity)
        if user != actor.subject_id:
            raise ActivityStaleOwner("The turn belongs to another human")
        try:
            live = await self._conversations.owns(attempt)
        except EventPermissionError as exc:
            raise ActivityUnavailable("Turn ownership is unknown") from exc
        if not live:
            raise ActivityStaleOwner("The turn no longer owns this conversation")
        return Causation(event_id=event_id, fence=attempt.fence, operation_id=operation_id)
