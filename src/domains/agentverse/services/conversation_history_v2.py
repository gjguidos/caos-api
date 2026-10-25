"""Authorized read and agent projection over the canonical transcript."""

import base64
import hashlib
import json

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.entities.conversation_v2 import ConversationV2
from src.domains.agentverse.entities.history_v2 import (
    AgentHistoryViewV2,
    ConversationMessageV2,
    HistoryPageV2,
)
from src.domains.agentverse.repositories.conversation_v2 import ConversationRepositoryV2
from src.domains.agentverse.security.utu import UTUAuthority


class ConversationHistoryV2:
    def __init__(self, conversations: ConversationRepositoryV2, authority: UTUAuthority) -> None:
        self.conversations = conversations
        self.authority = authority

    async def authorize(
        self,
        session: VerifiedAgentSession,
        conversation_id: str,
        agent_ids: tuple[str, ...] = (),
    ) -> ConversationV2:
        document = await self.conversations.get(session.tenant_id, conversation_id)
        await self.authority.authorize_conversation(
            session_id=session.session_id,
            conversation=document,
            agent_ids=agent_ids,
        )
        return document

    @staticmethod
    def visible(message: ConversationMessageV2, user: str, agent: str | None = None) -> bool:
        return (
            (not message.visible_user_ids or user in message.visible_user_ids)
            and (agent is None or agent in message.visible_agent_ids)
            and message.kind != "internal_event"
        )

    @staticmethod
    def cursor_scope(tenant: str, conversation: str) -> str:
        return hashlib.sha256(json.dumps([tenant, conversation]).encode()).hexdigest()

    async def page(
        self,
        session: VerifiedAgentSession,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> HistoryPageV2:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid history limit")
        scope = self.cursor_scope(session.tenant_id, conversation_id)
        after = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if token[0] != scope or type(token[1]) is not int or token[1] < 0:
                    raise ValueError("Cursor scope mismatch")
                after = token[1]
            except Exception as exc:
                raise ValueError("Invalid history cursor") from exc
        document = await self.authorize(session, conversation_id)
        rows = await self.conversations.read_messages(
            session.tenant_id,
            conversation_id,
            after=after,
            limit=limit,
        )
        current = await self.authorize(session, conversation_id)
        if current != document:
            raise EventPermissionError("Conversation changed during history read")
        # Scan positions refer to the canonical list, not the filtered result.
        next_cursor = None
        if len(rows) == limit:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([scope, rows[-1].sequence]).encode()
            ).decode()
        return HistoryPageV2(
            messages=tuple(m for m in rows if self.visible(m, session.user_id)),
            next_cursor=next_cursor,
        )

    async def project(
        self,
        session: VerifiedAgentSession,
        conversation_id: str,
        agent_id: str,
        *,
        exclude_event_id: str,
    ) -> AgentHistoryViewV2:
        document = await self.authorize(session, conversation_id, (agent_id,))
        rows = await self.conversations.read_messages(
            session.tenant_id,
            conversation_id,
            limit=100,
            tail=True,
        )
        current = await self.authorize(session, conversation_id, (agent_id,))
        if current != document:
            raise EventPermissionError("Conversation changed during projection")
        selected = []
        size = 0
        for message in reversed(rows):
            if (
                (message.event_id == exclude_event_id and message.turn_user_id == session.user_id)
                or not self.visible(message, session.user_id, agent_id)
                or message.kind not in ("user_message", "agent_response")
            ):
                continue
            if len(selected) == 24 or size + len(message.content) > 32768:
                break
            selected.append(message)
            size += len(message.content)
        return AgentHistoryViewV2(agent_id=agent_id, messages=tuple(reversed(selected)))
