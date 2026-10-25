"""Thin client-to-lifecycle/CPBX adapter; no cognitive recipe or orchestrator."""

import asyncio
import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2
from src.domains.agentverse.entities.history_v2 import ConversationMessageV2
from src.domains.agentverse.repositories.conversation_v2 import ConversationRepositoryV2
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey, AgentInstanceRegistryV2
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.cognitive_response_v2 import CognitiveResponseUnavailable
from src.domains.agentverse.services.conversation_history_v2 import ConversationHistoryV2

logger = logging.getLogger(__name__)


def response_for(event: ClientEventV2, kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_id": uuid4().hex,
        "conversation_id": event.conversation_id,
        "correlation_id": event.event_id,
        "type": kind,
        **payload,
    }


class ClientEventDispatcherV2:
    def __init__(
        self,
        *,
        conversations: ConversationRepositoryV2,
        authority: UTUAuthority,
        khepri: Khepri,
        instances: AgentInstanceRegistryV2,
    ) -> None:
        self.conversations = conversations
        self.authority = authority
        self.khepri = khepri
        self.instances = instances
        self.history = ConversationHistoryV2(conversations, authority)

    async def authorize(self, session: VerifiedAgentSession, event: ClientEventV2) -> None:
        conversation = await self.conversations.get(session.tenant_id, event.conversation_id)
        await self.authority.authorize_conversation(
            session_id=session.session_id,
            conversation=conversation,
            agent_ids=event.agent_ids,
        )
        if event.response_mode == "llm":
            for agent_id in event.agent_ids:
                principal = await self.authority.resolve_agent_principal(
                    AgentInstanceKey(
                        session.tenant_id, session.user_id, agent_id, session.session_id
                    )
                )
                if "chat.response" not in self.authority.grants_for(principal).invoke:
                    raise EventPermissionError("LLM response grant denied")

    async def dispatch(self, session: VerifiedAgentSession, event: ClientEventV2) -> dict[str, Any]:
        await self.authorize(session, event)
        admission = asyncio.create_task(
            self.conversations.begin(session.tenant_id, session.user_id, event)
        )
        try:
            status, cached = await asyncio.shield(admission)
        except asyncio.CancelledError:
            status, _ = await admission
            if status == "started":
                await self.conversations.finish(
                    session.tenant_id,
                    session.user_id,
                    event,
                    response_for(event, "event.error", code="cancelled"),
                )
            raise
        if status == "replay":
            return cached
        if status != "started":
            return response_for(event, "event.error", code=status)
        messages: tuple[ConversationMessageV2, ...] = ()
        document = None
        try:
            # Fixed capability and bounded targets; client cannot choose arbitrary signals.
            results = []
            async with asyncio.timeout(60 if event.response_mode == "llm" else 10):
                for agent_id in event.agent_ids:
                    await self.authorize(session, event)
                    key = AgentInstanceKey(
                        session.tenant_id, session.user_id, agent_id, session.session_id
                    )
                    await self.khepri.awaken(key)
                    async with self.instances.lock(key):
                        runtime = self.instances.get(key)
                        if runtime is None or not runtime.is_active:
                            raise EventPermissionError("Target runtime unavailable")
                        if event.response_mode == "llm":
                            result = await runtime.ncsc.request(
                                "chat.response",
                                {"text": event.message, "conversation_id": event.conversation_id},
                                correlation_id=event.event_id,
                                causation_id=event.event_id,
                            )
                            results.append(result)
                            continue
                        history = await self.history.project(
                            session,
                            event.conversation_id,
                            agent_id,
                            exclude_event_id=event.event_id,
                        )
                        context = await runtime.ncsc.request(
                            "chat.context",
                            {"text": event.message, "history": history.chat_messages()},
                            correlation_id=event.event_id,
                            causation_id=event.event_id,
                        )
                    results.append({"agent_id": agent_id, "context": context})
                await self.authorize(session, event)
            response = response_for(
                event,
                "chat.response" if event.response_mode == "llm" else "chat.context",
                results=results,
            )
            if response["type"] in {"chat.context", "chat.response"}:
                document = await self.history.authorize(
                    session, event.conversation_id, event.agent_ids
                )
                identity = hashlib.sha256(
                    json.dumps([session.user_id, event.event_id]).encode()
                ).hexdigest()
                shared = {
                    "tenant_id": session.tenant_id,
                    "conversation_id": event.conversation_id,
                    "event_id": event.event_id,
                    "turn_user_id": session.user_id,
                    "visible_agent_ids": event.agent_ids,
                }
                messages = (
                    ConversationMessageV2(
                        id=identity + "-user",
                        participant_id=session.user_id,
                        kind="user_message",
                        content=event.message,
                        **shared,
                    ),
                    *(
                        ConversationMessageV2(
                            id=identity + "-" + str(i),
                            participant_id=result["agent_id"],
                            kind="agent_response"
                            if event.response_mode == "llm"
                            else "capability_contribution",
                            capability=None if event.response_mode == "llm" else "chat.context",
                            content=result["response"]["content"]
                            if event.response_mode == "llm"
                            else json.dumps(result["context"], ensure_ascii=False),
                            visible_user_ids=(session.user_id,),
                            **shared,
                        )
                        for i, result in enumerate(response["results"])
                    ),
                )
        except asyncio.CancelledError:
            await self.conversations.finish(
                session.tenant_id,
                session.user_id,
                event,
                response_for(event, "event.error", code="cancelled"),
            )
            raise
        except EventPermissionError:
            response = response_for(event, "event.error", code="denied")
        except CognitiveResponseUnavailable as exc:
            response = response_for(event, "event.error", code=exc.code)
        except TimeoutError:
            response = response_for(event, "event.error", code="timeout")
        except Exception:  # noqa: BLE001 - safe transport boundary
            logger.error("V2 conversation turn failed")
            response = response_for(event, "event.error", code="unavailable")
        try:
            if messages:
                await self.authorize(session, event)
            await self.conversations.finish(
                session.tenant_id,
                session.user_id,
                event,
                response,
                messages=messages,
                expected_document=document,
            )
        except asyncio.CancelledError:
            # A submitted commit has been drained by the repository. If its document
            # CAS denied, settle the still-owned turn without any transcript batch.
            try:
                await self.conversations.finish(
                    session.tenant_id,
                    session.user_id,
                    event,
                    response_for(event, "event.error", code="cancelled"),
                )
            except EventPermissionError:
                pass  # An already committed turn has released its fence.
            raise
        except EventPermissionError:
            # A changed membership document must settle without partial transcript.
            response = response_for(event, "event.error", code="denied")
            await self.conversations.finish(session.tenant_id, session.user_id, event, response)
        return response
