from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.envelope import as_envelope as _as_envelope
from src.domains.agentverse.tools.tool_manager_v1.execution.local_tool_call_kwargs import (
    build_local_tool_call_kwargs as build_pure_call_kwargs,
)
from src.domains.agentverse.tools.tool_manager_v1.sanitize import (
    safe_model_dump as _safe_model_dump,
)


async def execute_pure_path(
    socket_emitter,
    manager: Any,
    *,
    key: str,
    inst: Any,
    params: set[str],
    param_specs: dict,
    requires_collection: bool,
    clean_text: str,
    flag_overrides: dict,
    history,
    session_id,
    request,
    policy,
    cfg_obj,
    meta: dict,
    ctx: dict,
    kwargs: dict,
    # injected helpers
    apply_config_defaults,
    get_cfg_value,
    coerce_val,
    maybe_tool_infer,
) -> dict | None:
    out_kwargs = await build_pure_call_kwargs(
        manager,
        key=key,
        inst=inst,
        params=params,
        param_specs=param_specs,
        requires_collection=requires_collection,
        clean_text=clean_text,
        flag_overrides=flag_overrides,
        history=history,
        session_id=session_id,
        request=request,
        policy=policy,
        cfg_obj=cfg_obj,
        meta=meta,
        ctx=ctx,
        kwargs=kwargs,
        apply_config_defaults=apply_config_defaults,
        get_cfg_value=get_cfg_value,
        coerce_val=coerce_val,
        maybe_tool_infer=maybe_tool_infer,
    )

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🫧 PURE] call_kwargs_ready",
        extra={
            "tool_key": key,
            "requires_collection": requires_collection,
            "kwarg_keys": list(out_kwargs.keys()) if isinstance(out_kwargs, dict) else [],
            "param_count": len(params) if params is not None else 0,
        },
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🫧 PURE] execute_start",
        extra={
            "tool_key": key,
            "instance_type": type(inst).__name__,
        },
    )

    try:
        result = await inst.execute(socket_emitter=socket_emitter, **out_kwargs)

        try:
            from src.domains.agentverse.tools.base import ToolResult

            if isinstance(result, ToolResult):
                result = _safe_model_dump(result)
        except Exception:
            pass

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][📦 ENVELOPE] envelope_build_start",
            extra={"tool_key": key},
        )

        envelope = _as_envelope(result)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🫧 PURE] execute_complete",
            extra={
                "tool_key": key,
                "result_type": type(result).__name__ if result is not None else None,
                "envelope_type": type(envelope).__name__ if envelope is not None else None,
            },
        )

        return envelope

    except Exception as e:
        AGENTVERSE_LOGGER.exception(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💥 ERROR] execute_failed",
            extra={
                "tool_key": key,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        return None
