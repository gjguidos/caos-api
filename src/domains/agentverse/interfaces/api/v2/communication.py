"""One authenticated V2 socket per session; conversations outlive its connection."""

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.conversation_v2 import (
    ClientEventV2,
    ConversationV2,
    CreateConversationV2,
    Identifier,
)
from src.domains.agentverse.entities.history_v2 import HistoryPageV2
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import (
    _authority,
    authenticated_genesis,
)
from src.domains.agentverse.services.client_event_dispatcher_v2 import response_for

router = APIRouter(tags=["Genesis Communication V2"])
BASE = "/api/v2/agentverse/communication"
logger = logging.getLogger(__name__)


class CancelEventV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["event.cancel"]
    conversation_id: Identifier
    event_id: Identifier
    cancel_event_id: Identifier


@router.post(BASE + "/ticket", status_code=201)
async def ticket(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with authenticated_genesis(request):
            value = await _authority(request).issue_ws_ticket()
            return {"ticket": value, "expires_in": 30, "websocket_path": "/ws/api/v2/caos"}
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis ticket denied") from exc


@router.post(BASE + "/conversations", status_code=201)
async def create_conversation(request: Request, body: CreateConversationV2) -> ConversationV2:
    try:
        async with authenticated_genesis(request) as session:
            av = request.app.container.agentverse
            conversation = ConversationV2(
                tenant_id=session.tenant_id,
                member_user_ids=(session.user_id,),
                agent_ids=body.agent_ids,
            )
            await av.utu_authority().authorize_conversation(
                session_id=session.session_id,
                conversation=conversation,
                agent_ids=body.agent_ids,
            )
            return await av.conversation_repository_v2().create(conversation)
    except EventPermissionError as exc:
        raise HTTPException(403, "Conversation denied") from exc


@router.get(BASE + "/conversations/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str) -> ConversationV2:
    try:
        async with authenticated_genesis(request) as session:
            av = request.app.container.agentverse
            conversation = await av.conversation_repository_v2().get(
                session.tenant_id,
                conversation_id,
            )
            await av.utu_authority().authorize_conversation(
                session_id=session.session_id,
                conversation=conversation,
            )
            return conversation
    except EventPermissionError as exc:
        raise HTTPException(403, "Conversation denied") from exc


@router.get(BASE + "/conversations/{conversation_id}/history")
async def get_history(
    request: Request,
    response: Response,
    conversation_id: Identifier,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
) -> HistoryPageV2:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with authenticated_genesis(request) as session:
            dispatcher = request.app.container.agentverse.client_event_dispatcher_v2()
            return await dispatcher.history.page(
                session,
                conversation_id,
                cursor=cursor,
                limit=limit,
            )
    except EventPermissionError as exc:
        raise HTTPException(403, "Conversation history denied") from exc
    except ValueError as exc:
        raise HTTPException(422, "Invalid history cursor") from exc


@router.websocket("/ws/api/v2/caos")
async def communication_socket(websocket: WebSocket) -> None:
    # No credentials in URLs or subprotocols. Unauthenticated sockets get one
    # bounded frame and five seconds to present a server-issued, single-use ticket.
    await websocket.accept()
    tasks: dict[tuple[str, str], tuple[ClientEventV2, asyncio.Task[None]]] = {}
    monitor: asyncio.Task[None] | None = None
    session = None
    repository = None
    owner = uuid4().hex
    claimed = False
    send_lock = asyncio.Lock()
    turn_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock, asyncio.timeout(2):
            await websocket.send_json(payload)

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 5)
        if len(raw.encode()) > 1024:
            raise EventPermissionError("Handshake too large")
        handshake = json.loads(raw)
        if (
            not isinstance(handshake, dict)
            or set(handshake) != {"type", "ticket"}
            or (handshake["type"] != "authenticate")
        ):
            raise EventPermissionError("Ticket handshake required")
        sessions = _authority(websocket)
        async with sessions.authenticated_ticket(handshake["ticket"]) as session:
            av = websocket.app.container.agentverse
            repository = av.conversation_repository_v2()
            dispatcher = av.client_event_dispatcher_v2()
            claimed = await repository.socket_lease(
                session.tenant_id, session.session_id, owner, "claim"
            )
            if not claimed:
                raise EventPermissionError("Session already connected")
            main = asyncio.current_task()
            assert main is not None

            async def check() -> None:
                await sessions.get_session(session.session_id)
                if not await repository.socket_lease(
                    session.tenant_id, session.session_id, owner, "renew"
                ):
                    raise EventPermissionError("Socket ownership lost")

            async def watch() -> None:
                try:
                    while True:
                        await asyncio.sleep(0.25)
                        await check()
                except EventPermissionError:
                    main.cancel()

            monitor = asyncio.create_task(watch())
            await send({"type": "session.ready", "session_id": session.session_id})

            async def execute(event: ClientEventV2) -> None:
                try:
                    async with turn_lock:
                        await check()
                        result = await dispatcher.dispatch(session, event)
                        await check()
                        # Recheck access even on durable replay and before output.
                        await dispatcher.authorize(session, event)
                        await send(result)
                except asyncio.CancelledError:
                    # Cancellation never emits context. A live client must still
                    # be able to settle the original request after agent revocation.
                    with suppress(
                        EventPermissionError, WebSocketDisconnect, RuntimeError, TimeoutError
                    ):
                        await check()
                        await send(response_for(event, "event.error", code="cancelled"))
                    raise
                except EventPermissionError:
                    await send(response_for(event, "event.error", code="denied"))
                except Exception:
                    logger.exception("V2 communication execution failed")
                    await websocket.close(code=1011)
                    main.cancel()
                finally:
                    tasks.pop((event.conversation_id, event.event_id), None)

            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > 16384:
                    await websocket.close(code=1009)
                    break
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("type") == "event.cancel":
                        cancel = CancelEventV2.model_validate(data)
                        pending = tasks.get((cancel.conversation_id, cancel.cancel_event_id))
                        if pending:
                            pending[1].cancel()
                            await asyncio.gather(pending[1], return_exceptions=True)
                        await send(
                            {
                                "type": "event.cancelled",
                                "event_id": uuid4().hex,
                                "conversation_id": cancel.conversation_id,
                                "correlation_id": cancel.event_id,
                                "cancel_event_id": cancel.cancel_event_id,
                            }
                        )
                        continue
                    event = ClientEventV2.model_validate(data)
                except (ValueError, ValidationError):
                    await websocket.close(code=1008)
                    break
                key = (event.conversation_id, event.event_id)
                if key in tasks:
                    kind = "pending" if tasks[key][0] == event else "conflict"
                    await send(response_for(event, "event.error", code=kind))
                elif len(tasks) >= 8:
                    await send(response_for(event, "event.error", code="backpressure"))
                else:
                    task = asyncio.create_task(execute(event))
                    tasks[key] = (event, task)
    except (EventPermissionError, ValueError, TimeoutError, asyncio.CancelledError):
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        pending_tasks = [task for _, task in tasks.values()]
        if monitor:
            monitor.cancel()
            pending_tasks.append(monitor)
        for task in pending_tasks:
            task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
        if claimed and repository is not None and session is not None:
            await repository.socket_lease(session.tenant_id, session.session_id, owner, "release")
