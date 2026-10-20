from __future__ import annotations

from src.base.handlers.exception import classify_error
from src.base.websockets.event_router import EventRouter
from src.domains.agentverse.context.connection_context import ConnCtx
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.services.agent_chat_service import AgentChatService


def register_message_events(event_router: EventRouter, ctx: ConnCtx | None = None) -> None:
    @event_router.on("joshu-a.comm")
    async def handle_send_message(data: dict):
        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A comm event handled.",
            extra={
                "event": "joshu-a.comm",
                "data_keys": list(data.keys()),
            },
        )
        return {
            "status": "🛰️ Joshu-A: Do you want to play a game?",
            "received": data.get("text", ""),
        }

    @event_router.on("joshu-a.create")
    async def handle_create_message(data: dict):
        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A create event received.",
            extra={
                "event": "joshu-a.create",
                "data_keys": list(data.keys()),
            },
        )

        svc = ctx.request.app.container.agentverse.divine_orchestration_service()
        await svc.create_agent(
            websocket=ctx.websocket,
            socket_emitter=ctx.socket_emitter,
            event_router=event_router,
            socket_id=ctx.session_id,
            **data,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.SOCKET_BRIDGE,
            "Joshu-A create event completed.",
            extra={"event": "joshu-a.create"},
        )

    if not ctx or not ctx.agent_system_name:
        AGENTVERSE_LOGGER.warning(
            LogDomain.SOCKET_BRIDGE,
            "Dynamic agent handler registration skipped; agent_system_name is missing.",
        )
        return

    event_id = f"{ctx.agent_system_name}.comm"

    AGENTVERSE_LOGGER.info(
        LogDomain.SOCKET_BRIDGE,
        "Registering dynamic EVA unit handler.",
        extra={"event": event_id},
    )

    @event_router.on(event_id)
    async def handle_agent_comm(data: dict):
        try:
            AGENTVERSE_LOGGER.debug(
                LogDomain.SOCKET_BRIDGE,
                "Dynamic EVA unit comm event received.",
                extra={
                    "event": event_id,
                    "data_keys": list(data.keys()),
                },
            )

            chat_service: AgentChatService = (
                ctx.request.app.container.agentverse.agent_chat_service()
            )

            reply = await chat_service.handle_chat(ctx=ctx, data=data)

            AGENTVERSE_LOGGER.debug(
                LogDomain.SOCKET_BRIDGE,
                "Dynamic EVA unit comm event completed.",
                extra={"event": event_id},
            )

            return {"status": "✅ Message sent", "message": reply}

        except Exception as e:
            AGENTVERSE_LOGGER.exception(
                LogDomain.SOCKET_BRIDGE,
                "Dynamic EVA unit comm event failed.",
                extra={"event": event_id},
            )
            message, code = classify_error(e)
            return {"status": "❌ Failed", "error": message, "code": code}

    AGENTVERSE_LOGGER.debug(
        LogDomain.SOCKET_BRIDGE,
        "Event handlers registered.",
        extra={"handler_keys": list(event_router.handlers.keys())},
    )
