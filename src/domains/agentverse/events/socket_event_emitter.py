from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocketDisconnect

from src.domains.agentverse.events.socket_contract import LogLevel, LogStage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SocketEventEmitter:
    def __init__(self, websocket, send_lock: asyncio.Lock):
        self.websocket = websocket
        self.send_lock = send_lock
        self._closed = False

    def mark_closed(self) -> None:
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def _safe_send(self, payload: dict) -> None:
        if self._closed:
            return

        try:
            async with self.send_lock:
                if self._closed:
                    return
                await self.websocket.send_json(payload)

        except (WebSocketDisconnect, RuntimeError) as e:
            self._closed = True
            logger.debug("Socket send skipped because websocket is closed: %s", e)

        except Exception as e:
            self._closed = True
            logger.warning("Unexpected websocket send failure: %s", e, exc_info=True)

    async def emit(self, event: str, payload: dict):
        await self._safe_send(
            {
                "type": "chat",
                "event": event,
                **payload,
            }
        )

    async def event(self, event: str, payload: dict):
        await self._safe_send(
            {
                "type": "event",
                "event": event,
                **payload,
            }
        )

    async def log(
        self,
        message: str,
        *,
        level: LogLevel,
        stage: LogStage | None = None,
    ):
        """Stream a user-facing log line to the client.

        ``level`` is required and ``message`` must be human-readable text: this
        is a declared contract, not something the client should have to parse.
        Internal reasoning and per-execution chatter belongs in the container
        logs, not here.
        """
        await self._safe_send(
            {
                "type": "log",
                "level": level,
                "stage": stage,
                "message": message,
                "ts": _now_iso(),
            }
        )

    async def error(self, event: str, payload: dict):
        await self._safe_send(
            {
                "type": "error",
                "event": event,
                **payload,
            }
        )


def resolve_socket_emitter(
    agent: Any = None,
    tool_kwargs: dict | None = None,
) -> SocketEventEmitter | None:
    tool_kwargs = tool_kwargs or {}

    emitter = tool_kwargs.get("_socket_emitter")
    if emitter is not None:
        return emitter

    if agent is not None:
        return getattr(agent, "socket_emitter", None)

    return None
