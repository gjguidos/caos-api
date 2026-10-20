from __future__ import annotations

import uuid

from fastapi import WebSocket

from src.base.middlewares.request_context import (
    request_context,
    reset_request_context,
    set_request_context,
)


def set_log_context(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    agent_system_name: str | None = None,
    event: str | None = None,
) -> None:
    """Add domain-specific fields to the shared request context.

    Always forks the context into a new dict and assigns a fresh request_id.
    This ensures that when called before asyncio.create_task(), each background
    task inherits its own isolated context so concurrent message handlers don't
    overwrite each other's request_id.

    Values set here are automatically injected into every log record by
    RequestContextFilter, alongside the base fields (request_id, user_object_id,
    etc.) that are set by init_websocket_context.
    """
    current = request_context.get(None)
    new_ctx = dict(current) if current else {}
    new_ctx["request_id"] = str(uuid.uuid4())
    if session_id is not None:
        new_ctx["session_id"] = session_id
    if user_id is not None:
        new_ctx["user_id"] = user_id
    if agent_id is not None:
        new_ctx["agent_id"] = agent_id
    if agent_system_name is not None:
        new_ctx["agent_system_name"] = agent_system_name
    if event is not None:
        new_ctx["event"] = event
    request_context.set(new_ctx)


def clear_log_context() -> None:
    """Reset all request context, including domain fields set by set_log_context."""
    reset_request_context()


def init_websocket_context(websocket: WebSocket) -> None:
    """Bootstrap request context for a WebSocket connection.

    Resets the context and populates user fields from the authenticated user
    stored in websocket.scope by the auth decorator. request_id is not set
    here - it is generated per message by set_log_context.
    """
    reset_request_context()

    user = websocket.scope.get("user")
    if user:
        set_request_context("user_object_id", user.id or "")
        set_request_context("user_roles", ",".join(user.roles))
