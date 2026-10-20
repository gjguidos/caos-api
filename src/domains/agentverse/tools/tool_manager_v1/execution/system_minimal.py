from __future__ import annotations

import inspect
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.envelope import as_envelope as _as_envelope
from src.domains.agentverse.tools.tool_manager_v1.policy import (
    is_system_tool_factories as _is_system_tool_factories,
)
from src.domains.agentverse.tools.tool_manager_v1.sanitize import json_sanitize as _json_sanitize


async def execute_system_minimal(socket_emitter, manager: Any, key: str, **kwargs):
    """
    Behavior-identical extraction of ToolManager.execute_system_minimal.

    - Ensures instance
    - System-only gate
    - Minimal runtime wiring + filtered kwargs passthrough
    - Normalizes ToolResult -> dict
    - Returns envelope with best-effort `envelope["result"]`
    """
    inst = manager.ensure_instance(key)
    if not inst:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚠️ SYSTEM] instance_missing",
            extra={
                "tool_key": key,
                "agent_id": getattr(manager.agent, "id", None),
                "agent_name": getattr(manager.agent, "name", None),
            },
        )
        return None

    if not _is_system_tool_factories(manager._factories, key):
        AGENTVERSE_LOGGER.error(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🚫 SYSTEM] non_system_tool_rejected",
            extra={"tool_key": key},
        )
        raise RuntimeError(
            f"execute_system_minimal() is system-only. Tool '{key}' is not tagged as a system tool."
        )

    text = kwargs.pop("text", "") or ""
    session_id = kwargs.pop("session_id", None)
    policy = kwargs.pop("policy", None)
    request = kwargs.pop("request", None)

    extra_eval = {k: kwargs[k] for k in ("question", "answer", "brief") if k in kwargs}

    if hasattr(inst, "set_runtime"):
        try:
            inst.set_runtime(agent=manager.agent, session_id=session_id, request=request)
        except Exception:
            setattr(inst, "agent", manager.agent)
            if session_id is not None:
                setattr(inst, "session_id", session_id)
            if request is not None:
                setattr(inst, "request", request)
    else:
        setattr(inst, "agent", manager.agent)
        if session_id is not None:
            setattr(inst, "session_id", session_id)
        if request is not None:
            setattr(inst, "request", request)

    params = set(inspect.signature(inst.execute).parameters.keys()) - {"self"}
    if hasattr(inst.__class__, "_run") and callable(getattr(inst.__class__, "_run")):
        params |= set(inspect.signature(inst._run).parameters.keys()) - {"self"}

    base = {
        "socket_emitter": socket_emitter,
        "text": text,
        "policy": policy,
        "agent": manager.agent,
        "session_id": session_id,
        "request": request,
        "allow_llm": True,
        **extra_eval,
    }
    out_kwargs = {k: v for k, v in base.items() if (k in params and v is not None)}

    passthrough = {k: v for k, v in kwargs.items() if k in params}
    out_kwargs.update(passthrough)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][⚙️ SYSTEM] execute_start",
        extra={
            "tool_key": key,
            "param_keys": sorted(out_kwargs.keys()),
        },
    )

    result = await inst.execute(**out_kwargs)

    try:
        from src.domains.agentverse.tools.base import ToolResult

        if isinstance(result, ToolResult):
            result = result.model_dump()
    except Exception:
        pass

    payload = _json_sanitize(result)

    if isinstance(payload, dict):
        tool_success = payload.get("success")
        tool_error = payload.get("error") or payload.get("message")

        envelope = _as_envelope(payload)
        envelope["result"] = payload.get("result") or payload.get("data") or payload
        envelope["raw"] = payload

        if tool_success is False:
            envelope["success"] = False
            envelope["error"] = tool_error

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚙️ SYSTEM] execute_complete",
            extra={
                "tool_key": key,
                "success": tool_success if tool_success is not None else True,
            },
        )
        return envelope

    envelope = _as_envelope(result)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][⚙️ SYSTEM] execute_complete",
        extra={
            "tool_key": key,
            "success": True,
        },
    )
    return envelope
