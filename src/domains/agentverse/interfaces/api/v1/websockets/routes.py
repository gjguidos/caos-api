from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.base.authentication.permission import Permission
from src.base.authentication.role import Role
from src.base.decorators.middleware.ws_session_based import session_based_ws
from src.base.events.channels import user_event_channel
from src.base.websockets.event_router import EventRouter
from src.domains.agentverse.cache import helpers as av_cache
from src.domains.agentverse.context.connection_context import ConnCtx
from src.domains.agentverse.events.message_events import register_message_events
from src.domains.agentverse.events.socket_event_emitter import SocketEventEmitter
from src.domains.agentverse.logging.context import (
    clear_log_context,
    init_websocket_context,
    set_log_context,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain

router = APIRouter()
connected_clients = set()
MAX_INFLIGHT = 4


@router.websocket("/ws/api/v1/Joshu-A")
@session_based_ws(
    required_roles=[[Role.USER]],
    required_permissions=[Permission.AGENT_WRITE],
)
async def ws_handler(websocket: WebSocket):
    init_websocket_context(websocket)
    event_router = EventRouter()
    session_id = str(uuid4())
    send_lock = asyncio.Lock()
    socket_emitter = SocketEventEmitter(websocket, send_lock)
    commbridge = websocket.app.state.cognitive_modules["communication"]["socketRedisBridge"]

    AGENTVERSE_LOGGER.info(
        LogDomain.SOCKET_BRIDGE,
        "Joshu-A socket bridge connected.",
    )

    bridge_connected = False
    try:
        authenticated_user = websocket.scope.get("user")
        user_id = getattr(authenticated_user, "id", None)

        if not user_id:
            raise RuntimeError(
                "Authenticated WebSocket has no user_id."
            )

        user_id = str(user_id)
        channel = user_event_channel(user_id)

        await commbridge.connect(session_id, websocket, send_lock)
        bridge_connected = True
        await commbridge.subscribe(
            channel=channel,
            socket_id=session_id,
        )

        set_log_context(
            session_id=session_id,
            agent_system_name="joshu-a",
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A websocket session starting.",
        )
        tenant_id = websocket.scope.get("tenant_id")

        ctx = ConnCtx(
            session_id=session_id,
            socket_emitter=socket_emitter,
            user_id=user_id,
            agent_id=None,
            request=websocket,
            websocket=websocket,
            tenant_id=tenant_id,
            agent_system_name="joshu-a",
        )
        register_message_events(event_router, ctx)

        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A message handlers registered.",
        )

        while True:
            msg = await websocket.receive_json()
            event = (msg.get("event") or "").lower()
            set_log_context(event=event)
            data = msg.get("data") or {}
            data.setdefault("_session_id", session_id)

            AGENTVERSE_LOGGER.debug(
                LogDomain.SOCKET_BRIDGE,
                "Joshu-A event received.",
                extra={"data_keys": list(data.keys())},
            )

            handler = event_router.get(event)
            if not handler:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.SOCKET_BRIDGE,
                    "No websocket handler found.",
                )
                await socket_emitter.emit(event, {"error": f"No handler for event '{event}'"})
                continue

            try:
                resp = await handler(data)
                await socket_emitter.emit(event, {"response": resp})

                AGENTVERSE_LOGGER.debug(
                    LogDomain.SOCKET_BRIDGE,
                    "Joshu-A event handled.",
                )
            except Exception:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.SOCKET_BRIDGE,
                    "Joshu-A handler failed.",
                )
                await socket_emitter.emit(event, {"error": "Internal handler error"})
    except WebSocketDisconnect:
        socket_emitter.mark_closed()
        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A websocket disconnected.",
        )
    finally:
        try:
            socket_emitter.mark_closed()
            if bridge_connected:
                await commbridge.disconnect(session_id)
                AGENTVERSE_LOGGER.info(
                    LogDomain.SOCKET_BRIDGE,
                    "Joshu-A socket bridge disconnected.",
                )
        finally:
            clear_log_context()


@router.websocket("/ws/api/v1/agent/{agent_system_name}")
@session_based_ws(
    required_roles=[[Role.USER]],
    required_permissions=[Permission.AGENT_READ],
)
async def ws_chat_handler(websocket: WebSocket, agent_system_name: str):
    init_websocket_context(websocket)
    connected_clients.add(websocket)

    send_lock = asyncio.Lock()
    socket_emitter = SocketEventEmitter(websocket, send_lock)
    inflight_sem = asyncio.Semaphore(MAX_INFLIGHT)
    bg_tasks: set[asyncio.Task] = set()

    event_router = EventRouter()
    session_id = str(uuid4())
    commbridge = websocket.app.state.cognitive_modules["communication"]["socketRedisBridge"]

    bridge_connected = False

    # Cleanup state must always exist
    hb_stop = asyncio.Event()
    hb_task: asyncio.Task | None = None
    watchdog_task: asyncio.Task | None = None
    agent_id: str | None = None

    user_id: str | None = None

    try:
        authenticated_user = websocket.scope.get("user")
        user_id = getattr(authenticated_user, "id", None)

        if not user_id:
            raise RuntimeError("Authenticated WebSocket has no user_id.")

        user_id = str(user_id)
        channel = user_event_channel(user_id)

        await commbridge.connect(session_id, websocket, send_lock)
        bridge_connected = True
        await commbridge.subscribe(
            channel=channel,
            socket_id=session_id,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            f"Agent socket subscribed to user events. {channel}",
            extra={
                "user_id": user_id,
                "channel": user_event_channel,
            },
        )

        set_log_context(
            session_id=session_id,
            agent_system_name=agent_system_name.lower(),
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Agent websocket session starting.",
            extra={"path_agent_system_name": agent_system_name},
        )

        first_message = await websocket.receive_json()
        event = (first_message.get("event", "") or "").lower()
        data = (first_message.get("data") or {}) | {"_session_id": session_id}

        agent_system_name = (
            first_message.get("agent_system_name") or event.split(".")[0] or agent_system_name or ""
        ).lower()
        
        agent_id = first_message.get("agent_id")

        set_log_context(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_system_name=agent_system_name,
            event=event,
        )

        tenant_id = websocket.scope.get("tenant_id")

        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Agent websocket connected.",
        )

        ctx = ConnCtx(
            session_id=session_id,
            websocket=websocket,
            request=websocket,
            socket_emitter=socket_emitter,
            user_id=user_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
            agent_system_name=agent_system_name,
        )

        register_message_events(event_router=event_router, ctx=ctx)

        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE,
            "Agent message handlers registered.",
        )

        if user_id and agent_id:
            await av_cache.presence_join(
                websocket,
                user_id,
                agent_id,
                session_id,
                session_id=session_id,
            )
            await av_cache.touch_spawn(websocket, user_id, agent_id, session_id=session_id)
            hb_task = asyncio.create_task(
                _presence_heartbeat(websocket, user_id, agent_id, session_id, hb_stop)
            )

            AGENTVERSE_LOGGER.info(
                LogDomain.SOCKET_BRIDGE,
                "Presence tracking started.",
            )

        async def handle_and_send(msg_event: str, msg_data: dict):
            handler = event_router.get(msg_event)
            if not handler:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.SOCKET_BRIDGE,
                    "No websocket handler found.",
                )
                await socket_emitter.emit(
                    msg_event, {"error": f"No handler for event '{msg_event}'"}
                )
                return

            try:
                async with inflight_sem:
                    AGENTVERSE_LOGGER.debug(
                        LogDomain.SOCKET_BRIDGE,
                        "Executing websocket handler.",
                        extra={"data_keys": list(msg_data.keys())},
                    )

                    response = await handler(msg_data)
                    await socket_emitter.emit(msg_event, {"response": response})

                    AGENTVERSE_LOGGER.debug(
                        LogDomain.SOCKET_BRIDGE,
                        "Websocket handler completed.",
                    )
            except Exception:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.SOCKET_BRIDGE,
                    "Websocket handler failed.",
                )
                await socket_emitter.emit(msg_event, {"error": "Internal handler error"})

        t = asyncio.create_task(handle_and_send(event, data))
        bg_tasks.add(t)
        t.add_done_callback(bg_tasks.discard)

        async def _loop_watchdog() -> None:
            while True:
                started = time.perf_counter()

                await asyncio.sleep(0.2)

                elapsed_ms = (
                    time.perf_counter() - started
                ) * 1000

                blocked_ms = max(
                    0.0,
                    elapsed_ms - 200.0,
                )

                if blocked_ms > 800.0:
                    AGENTVERSE_LOGGER.warning(
                        LogDomain.SOCKET_BRIDGE,
                        (
                            "Event loop stall detected. "
                            f"blocked_ms={blocked_ms:.2f}"
                        ),
                        extra={
                            "blocked_ms": round(
                                blocked_ms,
                                2,
                            ),
                            "watchdog_interval_ms": 200,
                        },
                    )

        watchdog_task = asyncio.create_task(_loop_watchdog())

        
        while True:
            msg = await websocket.receive_json()
            msg_event = (msg.get("event", "") or "").lower()
            set_log_context(event=msg_event)
            msg_data = (msg.get("data") or {}) | {"_session_id": session_id}

            AGENTVERSE_LOGGER.debug(
                LogDomain.SOCKET_BRIDGE,
                "Websocket message received.",
                extra={"data_keys": list(msg_data.keys())},
            )

            if user_id and agent_id:
                await av_cache.presence_heartbeat(
                    websocket,
                    user_id,
                    agent_id,
                    session_id=session_id,
                )
                await av_cache.touch_spawn(websocket, user_id, agent_id, session_id=session_id)

            t = asyncio.create_task(handle_and_send(msg_event, msg_data))
            bg_tasks.add(t)
            t.add_done_callback(bg_tasks.discard)
    except WebSocketDisconnect:
        socket_emitter.mark_closed()
        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Agent websocket disconnected by client.",
        )
    finally:
        socket_emitter.mark_closed()
        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.SOCKET_BRIDGE,
                "Agent websocket cleanup started.",
                extra={
                    "bg_task_count": len(bg_tasks),
                    "heartbeat_active": hb_task is not None,
                },
            )

            if watchdog_task is not None:
                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)

            for t in list(bg_tasks):
                t.cancel()
            if bg_tasks:
                await asyncio.gather(*bg_tasks, return_exceptions=True)

            if hb_task:
                hb_stop.set()
                try:
                    await asyncio.wait_for(hb_task, timeout=3)
                except TimeoutError:
                    AGENTVERSE_LOGGER.warning(
                        LogDomain.SOCKET_BRIDGE,
                        "Heartbeat task shutdown timed out.",
                    )
                    hb_task.cancel()
                    await asyncio.gather(hb_task, return_exceptions=True)

            if user_id and agent_id:
                remaining = await av_cache.presence_leave(
                    websocket,
                    user_id,
                    agent_id,
                    session_id,
                    session_id=session_id,
                )

                AGENTVERSE_LOGGER.info(
                    LogDomain.SOCKET_BRIDGE,
                    "Presence leave processed.",
                    extra={"remaining_sockets": remaining},
                )

                if remaining == 0:
                    async with av_cache.acquire_spawn_lock(
                        websocket,
                        user_id,
                        agent_id,
                        session_id=session_id,
                    ):
                        repo = av_cache.get_cache_repo(websocket)
                        key = av_cache.presence_sockets_key(
                            websocket,
                            user_id,
                            agent_id,
                            session_id=session_id,
                        )
                        left = await repo.scard(key) or 0

                        AGENTVERSE_LOGGER.debug(
                            LogDomain.SOCKET_BRIDGE,
                            "Presence socket count rechecked under lock.",
                            extra={"remaining_locked": left},
                        )

                        if left == 0:
                            reg = websocket.app.container.agentverse.instance_registry
                            agent = reg.get(user_id, agent_id, session_id)

                            if agent:
                                AGENTVERSE_LOGGER.info(
                                    LogDomain.SOCKET_BRIDGE,
                                    "Putting runtime agent to sleep.",
                                )
                                await agent.sleep(websocket)
                                reg.remove(user_id, agent_id, session_id)

                                AGENTVERSE_LOGGER.info(
                                    LogDomain.SOCKET_BRIDGE,
                                    "Runtime agent removed from registry.",
                                )
        finally:
            try:
                if bridge_connected:
                    await commbridge.disconnect(session_id)
                    AGENTVERSE_LOGGER.info(
                        LogDomain.SOCKET_BRIDGE,
                        "Agent socket bridge disconnected.",
                    )
            finally:
                clear_log_context()
                connected_clients.discard(websocket)


async def _presence_heartbeat(request, user_id, agent_id, session_id, stop_evt: asyncio.Event):
    AGENTVERSE_LOGGER.debug(
        LogDomain.SOCKET_BRIDGE,
        "Presence heartbeat started.",
    )
    try:
        while not stop_evt.is_set():
            await av_cache.presence_heartbeat(request, user_id, agent_id, session_id=session_id)
            await av_cache.touch_spawn(request, user_id, agent_id, session_id=session_id)
            await asyncio.sleep(30)
    except Exception:
        AGENTVERSE_LOGGER.exception(
            LogDomain.SOCKET_BRIDGE,
            "Presence heartbeat failed.",
        )
    finally:
        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE,
            "Presence heartbeat stopped.",
        )
