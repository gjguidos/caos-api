"""Thin client-to-lifecycle/CPBX adapter; no cognitive recipe or orchestrator."""

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from typing import Any
from uuid import uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.activities.models import (
    ActivityContractUnavailable,
    ActivityError,
    ActivityNotFound,
)
from src.domains.agentverse.agents.conversational_composition_v2 import (
    ConversationGate,
    ConversationUnavailable,
    DefinitionChanged,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.entities.conversation_v2 import ActivityStartV2, ClientEventV2
from src.domains.agentverse.entities.history_v2 import ConversationMessageV2
from src.domains.agentverse.repositories.conversation_v2 import (
    ConversationRepositoryV2,
    TurnLease,
    TurnOwnershipLost,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey, AgentInstanceRegistryV2
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.cognitive_response_v2 import CognitiveResponseUnavailable
from src.domains.agentverse.services.conversation_history_v2 import ConversationHistoryV2
from src.domains.agentverse.services.creation_interview_coordinator_v2 import CoordinatorError

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
        composition: ConversationGate,
    ) -> None:
        self.conversations = conversations
        self.authority = authority
        self.khepri = khepri
        self.instances = instances
        # CONVERSATION-0.1A: registry-driven composition per target Agent and mode.
        self.composition = composition
        self.history = ConversationHistoryV2(conversations, authority)
        # COORD-0.1: activity coordinators by exact activity key, bound at trusted startup.
        # Unbound (the default), every activity event fails closed.
        self._coordinators: dict[str, Any] = {}

    def bind_activity_coordinator(self, coordinator: Any) -> None:
        existing = self._coordinators.get(coordinator.activity)
        if existing is not None and existing is not coordinator:
            raise ValueError("An activity coordinator is already bound for this activity")
        self._coordinators[coordinator.activity] = coordinator

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
            status, lease = await admission
            if status == "started":
                with suppress(EventPermissionError):
                    await self.conversations.finish(
                        session.tenant_id,
                        session.user_id,
                        event,
                        response_for(event, "event.error", code="cancelled"),
                        lease=lease,
                    )
            raise
        if status == "replay":
            return cached
        if status != "started":
            return response_for(event, "event.error", code=status)
        lost = asyncio.Event()
        work = asyncio.create_task(self._execute(session, event, cached))
        keeper = asyncio.create_task(self._keep(cached, work, lost))
        try:
            return await work
        except (asyncio.CancelledError, EventPermissionError) as exc:
            current = asyncio.current_task()
            stale = lost.is_set() or isinstance(exc, TurnOwnershipLost)
            if not stale or (current is not None and current.cancelling()):
                raise
            # Ownership was lost: only the journal's recorded result is authoritative.
            return await self._recorded(session, event)
        finally:
            keeper.cancel()
            await asyncio.gather(keeper, return_exceptions=True)

    async def _keep(self, lease: TurnLease, work: asyncio.Task[Any], lost: asyncio.Event) -> None:
        """Renew the owned lease; on loss (or unknown storage state) stop the stale worker."""
        interval = self.conversations.lease_ms / 3000
        while not work.done():
            await asyncio.sleep(interval)
            try:
                renewed = await self.conversations.renew(lease)
            except EventPermissionError:
                renewed = False
            if not renewed and not work.done():
                lost.set()
                work.cancel()
                return

    async def _recorded(
        self, session: VerifiedAgentSession, event: ClientEventV2
    ) -> dict[str, Any]:
        status, cached = await self.conversations.begin(session.tenant_id, session.user_id, event)
        if status == "replay":
            return cached
        # Still owned by a live attempt or unreadable: never invent a terminal result.
        return response_for(event, "event.error", code="pending" if status == "pending" else status)

    async def _activity(
        self, session: VerifiedAgentSession, event: ClientEventV2, lease: TurnLease
    ) -> dict[str, Any]:
        """Hand an explicit activity event to the coordinator of its exact activity. The
        coordinator acts through UTU-resolved, attenuated authority only."""
        request = event.activity
        if isinstance(request, ActivityStartV2):
            coordinator = self._coordinators.get(request.key)
            if coordinator is None:
                raise ActivityContractUnavailable("No coordinator for this activity")
            outcome, binding = await coordinator.start(session, event, lease)
            # Journaled with the immutable outcome: the definition this turn ran under.
            return response_for(
                event, "activity.state", result=outcome, definitions=[binding.wire()]
            )
        for coordinator in self._coordinators.values():
            try:
                outcome = await coordinator.close(session, event, lease)
            except ActivityNotFound:
                continue
            return response_for(event, "activity.state", result=outcome)
        raise ActivityNotFound("Activity context not found")

    async def _execute(
        self, session: VerifiedAgentSession, event: ClientEventV2, lease: TurnLease
    ) -> dict[str, Any]:
        messages: tuple[ConversationMessageV2, ...] = ()
        document = None
        try:
            if event.type != "chat.message":
                # COORD-0.1: explicit, typed activity events; no transcript, no model.
                async with asyncio.timeout(10):
                    response = await self._activity(session, event, lease)
                    await self.authorize(session, event)
            else:
                # Fixed capability and bounded targets; client cannot choose arbitrary signals.
                results = []
                bindings = []
                async with asyncio.timeout(60 if event.response_mode == "llm" else 10):
                    for agent_id in event.agent_ids:
                        await self.authorize(session, event)
                        # Availability, not authority: grants were checked by ``authorize``
                        # (chat.response) and are checked again by each provider.
                        # REV-5: bind the authoritative definition at admission; Tools and their
                        # configuration come from that one read.
                        config, binding = await self.composition.admit(
                            session.tenant_id, agent_id, event.response_mode
                        )
                        bindings.append(binding)
                        key = AgentInstanceKey(
                            session.tenant_id, session.user_id, agent_id, session.session_id
                        )
                        await self.khepri.awaken(key)
                        async with self.instances.lock(key):
                            runtime = self.instances.get(key)
                            if runtime is None or not runtime.is_active:
                                raise EventPermissionError("Target runtime unavailable")
                            if event.response_mode == "llm":
                                # Freshness before execution; the provider re-verifies the
                                # binding against its own read immediately before inference.
                                await self.composition.ensure_current(
                                    session.tenant_id, agent_id, binding, event.response_mode
                                )
                                result = await runtime.ncsc.request(
                                    "chat.response",
                                    {
                                        "text": event.message,
                                        "conversation_id": event.conversation_id,
                                        "definition": binding.wire(),
                                    },
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
                                max_messages=config.history.max_messages,
                                max_characters=config.history.max_characters,
                            )
                            await self.composition.ensure_current(
                                session.tenant_id, agent_id, binding, event.response_mode
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
                    # Journaled with the immutable outcome: the definitions this turn ran under.
                    definitions=[b.wire() for b in bindings],
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
                            content=event.message or "",
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
            # A lost lease makes this commit fail closed; the recorded result wins.
            with suppress(EventPermissionError):
                await self.conversations.finish(
                    session.tenant_id,
                    session.user_id,
                    event,
                    response_for(event, "event.error", code="cancelled"),
                    lease=lease,
                )
            raise
        except EventPermissionError:
            response = response_for(event, "event.error", code="denied")
        except (ActivityError, CoordinatorError) as exc:
            response = response_for(
                event,
                "event.error",
                code=exc.code,
                **({"retryable": True} if exc.retryable else {}),
            )
        except DefinitionChanged as exc:
            # Retryable only as a new turn (new event ID); this outcome is immutable and
            # its replay never re-executes anything.
            response = response_for(event, "event.error", code=exc.code, retryable=True)
        except (CognitiveResponseUnavailable, ConversationUnavailable) as exc:
            response = response_for(
                event,
                "event.error",
                code=exc.code,
                **({"retryable": True} if exc.code == "definition_changed" else {}),
            )
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
                lease=lease,
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
                    lease=lease,
                )
            except EventPermissionError:
                pass  # An already committed turn has released its fence.
            raise
        except EventPermissionError:
            # A changed membership document must settle without partial transcript.
            response = response_for(event, "event.error", code="denied")
            await self.conversations.finish(
                session.tenant_id, session.user_id, event, response, lease=lease
            )
        return response
