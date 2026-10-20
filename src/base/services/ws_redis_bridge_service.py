import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class SocketRedisBridgeService:
    def __init__(self, redis_repository):
        self.redis_repository = redis_repository
        self.active_connections: dict[str, WebSocket] = {}
        self.send_locks: dict[str, asyncio.Lock] = {}
        self.channel_tasks: dict[
            tuple[str, str],
            asyncio.Task,
        ] = {}

    async def connect(
        self,
        socket_id: str,
        websocket: WebSocket,
        send_lock: asyncio.Lock | None = None,
    ) -> None:
        self.active_connections[socket_id] = websocket
        self.send_locks[socket_id] = (
            send_lock or asyncio.Lock()
        )

    async def disconnect(
        self,
        socket_id: str,
    ) -> None:
        for (sid, channel), task in list(
            self.channel_tasks.items()
        ):
            if sid == socket_id:
                task.cancel()
                self.channel_tasks.pop(
                    (sid, channel),
                    None,
                )

        self.send_locks.pop(socket_id, None)

        websocket = self.active_connections.pop(
            socket_id,
            None,
        )

        if websocket:
            try:
                await websocket.close()
            except Exception:
                pass

    async def subscribe(
        self,
        channel: str,
        socket_id: str,
    ) -> None:
        key = (socket_id, channel)

        if key in self.channel_tasks:
            return

        async def listener():
            logger.info(
                "Socket Redis bridge subscribing. socket_id=%s channel=%s",
                socket_id,
                channel,
            )
            try:
                async for message in (
                    self.redis_repository.subscribe_channel(
                        channel,
                    )
                ):
                    if socket_id not in self.active_connections:
                        break

                    try:
                        await self.send_to_socket(
                            socket_id,
                            message,
                        )
                    except Exception:
                        break
            finally:
                self.channel_tasks.pop(key, None)

        self.channel_tasks[key] = asyncio.create_task(
            listener(),
            name=f"sub:{socket_id}:{channel}",
        )

    async def unsubscribe(
        self,
        channel: str,
        socket_id: str,
    ) -> None:
        key = (socket_id, channel)
        task = self.channel_tasks.pop(key, None)

        if task:
            task.cancel()

    async def send_to_socket(
        self,
        socket_id: str,
        message: str,
    ) -> None:
        websocket = self.active_connections.get(socket_id)
        lock = self.send_locks.get(socket_id)

        if not websocket or not lock:
            return

        async with lock:
            await websocket.send_text(message)

    async def send_json(
        self,
        socket_id: str,
        payload: Any,
    ) -> None:
        await self.send_to_socket(
            socket_id,
            json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
            ),
        )

    async def broadcast_to_all(
        self,
        message: str,
    ) -> None:
        for socket_id in list(self.active_connections.keys()):
            try:
                await self.send_to_socket(
                    socket_id,
                    message,
                )
            except Exception:
                pass

    async def broadcast_json_to_all(
        self,
        payload: Any,
    ) -> None:
        await self.broadcast_to_all(
            json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
            )
        )