# src/domains/agentverse/agents/orchestrator/turn_context.py

from __future__ import annotations

from typing import Any

from src.domains.agentverse.events.socket_event_emitter import resolve_socket_emitter


def resolve_turn_context(
    ctx: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    ctx = ctx or {}
    extra = extra or {}

    agent = extra.get("agent") or ctx.get("agent")
    tool_manager = extra.get("tool_manager") or ctx.get("tool_manager")

    tool_kwargs = extra.get("tool_kwargs") or extra.get("kwargs") or ctx.get("tool_kwargs") or {}

    socket_emitter = (
        extra.get("_socket_emitter")
        or extra.get("socket_emitter")
        or ctx.get("_socket_emitter")
        or ctx.get("socket_emitter")
        or resolve_socket_emitter(agent=agent, tool_kwargs=tool_kwargs)
    )

    session_id = (
        extra.get("_session_id")
        or extra.get("session_id")
        or ctx.get("_session_id")
        or ctx.get("session_id")
        or tool_kwargs.get("_session_id")
    )

    agent_id = (
        extra.get("_agent_id")
        or extra.get("agent_id")
        or ctx.get("_agent_id")
        or ctx.get("agent_id")
        or tool_kwargs.get("_agent_id")
        or getattr(agent, "id", None)
    )

    return {
        "agent": agent,
        "tool_manager": tool_manager,
        "tool_kwargs": tool_kwargs,
        "socket_emitter": socket_emitter,
        "session_id": session_id,
        "agent_id": agent_id,
    }
