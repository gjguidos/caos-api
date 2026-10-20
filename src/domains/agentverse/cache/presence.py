"""Socket presence lifecycle for agent sessions.

`helpers.py` owns the Redis keys and primitives; this module owns the policy built on
top of them: while a socket is connected the agent is marked present and kept warm, and
when the last socket for an agent goes away the runtime instance is put to sleep.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import WebSocket

from src.domains.agentverse.cache import helpers as av_cache
from src.domains.agentverse.dependencies.get_instance_registry import get_instance_registry
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain

# Comfortably inside `helpers.presence_ttl` (90s), so presence survives a missed beat.
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS = 3


async def _touch(socket: WebSocket, user_id: str, agent_id: str, session_id: str) -> None:
    """Refresh the presence TTL and the agent's spawn marker."""
    await av_cache.presence_heartbeat(socket, user_id, agent_id, session_id=session_id)
    await av_cache.touch_spawn(socket, user_id, agent_id, session_id=session_id)


async def _heartbeat(
    socket: WebSocket, user_id: str, agent_id: str, session_id: str, stop: asyncio.Event
) -> None:
    AGENTVERSE_LOGGER.debug(LogDomain.SOCKET_BRIDGE, "Presence heartbeat started.")
    try:
        while not stop.is_set():
            await _touch(socket, user_id, agent_id, session_id)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
    except Exception:
        AGENTVERSE_LOGGER.exception(LogDomain.SOCKET_BRIDGE, "Presence heartbeat failed.")
    finally:
        AGENTVERSE_LOGGER.debug(LogDomain.SOCKET_BRIDGE, "Presence heartbeat stopped.")


async def _stop_heartbeat(hb_task: asyncio.Task, stop: asyncio.Event) -> None:
    stop.set()
    try:
        await asyncio.wait_for(hb_task, timeout=HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        AGENTVERSE_LOGGER.warning(LogDomain.SOCKET_BRIDGE, "Heartbeat task shutdown timed out.")
        hb_task.cancel()
        await asyncio.gather(hb_task, return_exceptions=True)


async def _release(socket: WebSocket, user_id: str, agent_id: str, session_id: str) -> None:
    """Drop this socket from presence; sleep the agent if it was the last one."""
    remaining = await av_cache.presence_leave(
        socket, user_id, agent_id, session_id, session_id=session_id
    )
    AGENTVERSE_LOGGER.info(
        LogDomain.SOCKET_BRIDGE,
        "Presence leave processed.",
        extra={"remaining_sockets": remaining},
    )
    if remaining != 0:
        return

    async with av_cache.acquire_spawn_lock(socket, user_id, agent_id, session_id=session_id):
        repo = av_cache.get_cache_repo(socket)
        key = av_cache.presence_sockets_key(socket, user_id, agent_id, session_id=session_id)
        left = await repo.scard(key) or 0

        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE,
            "Presence socket count rechecked under lock.",
            extra={"remaining_locked": left},
        )
        if left != 0:
            return

        registry = get_instance_registry(socket)
        agent = registry.get(user_id, agent_id, session_id)
        if not agent:
            return

        AGENTVERSE_LOGGER.info(LogDomain.SOCKET_BRIDGE, "Putting runtime agent to sleep.")
        await agent.sleep(socket)
        registry.remove(user_id, agent_id, session_id)
        AGENTVERSE_LOGGER.info(LogDomain.SOCKET_BRIDGE, "Runtime agent removed from registry.")


@asynccontextmanager
async def presence_session(
    socket: WebSocket, user_id: str | None, agent_id: str | None, session_id: str
) -> AsyncIterator[None]:
    """Mark this socket present for the agent and heartbeat until the block exits.

    Without both a user and an agent there is nothing to track and the block runs
    untouched, so callers never need to branch on it. Otherwise the heartbeat owns the
    presence TTL for the life of the block - callers must not refresh it per message.
    """
    if not (user_id and agent_id):
        AGENTVERSE_LOGGER.debug(
            LogDomain.SOCKET_BRIDGE, "Presence tracking skipped: no user/agent on this socket."
        )
        yield
        return

    await av_cache.presence_join(socket, user_id, agent_id, session_id, session_id=session_id)
    await av_cache.touch_spawn(socket, user_id, agent_id, session_id=session_id)

    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(socket, user_id, agent_id, session_id, stop))

    AGENTVERSE_LOGGER.info(LogDomain.SOCKET_BRIDGE, "Presence tracking started.")
    try:
        yield
    finally:
        await _stop_heartbeat(hb_task, stop)
        await _release(socket, user_id, agent_id, session_id)
