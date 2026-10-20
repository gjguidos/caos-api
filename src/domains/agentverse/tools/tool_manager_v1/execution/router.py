from __future__ import annotations

import time
from typing import Any

from src.domains.agentverse.events.socket_event_emitter import SocketEventEmitter
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.execution.envelope import error_envelope
from src.domains.agentverse.tools.tool_manager_v1.policy import evaluate_invocation_scope
from src.domains.agentverse.utils.redaction import summarize_args
from src.domains.agentverse.utils.run_trace import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_VETOED,
    TRACE_KEY,
    record_step,
    resolve_sink,
    utc_now_iso,
)


async def execute_router(
    socket_emitter: SocketEventEmitter,
    manager: Any,
    *,
    key: str,
    kwargs: dict,
    normalize_execute_inputs,
    pop_common_execute_kwargs,
    build_effective_context,
    apply_depth_guard,
    evaluate_policy,
    route_for_key,
    execute_system_minimal,
    execute_invoker_path,
    prepare_pure_prep,
    execute_pure_path,
    collect_param_specs,
    extract_cli_overrides,
    strip_tool_anchor,
    flag_re,
    resolve_param_name,
    strip_quotes,
    coerce_val,
    apply_config_defaults,
    get_cfg_value,
    maybe_tool_infer,
):
    """Record one step per execution, then delegate to the real router.

    Every phase handler and all three execution paths funnel through here, so
    this is the only place the turn's tool telemetry has to be collected. The
    ``finally`` matters: a policy veto or a depth-guard block returns an error
    envelope rather than raising, and both must still show up as steps.

    Arguments are summarized here, on the way in, rather than in the ``finally``
    - ``_route`` pops keys off ``kwargs`` as it goes, and the trace sink itself
    grows while the tool runs, so a late read would record neither the arguments
    as given nor a stable snapshot.
    """
    sink = resolve_sink(kwargs, kwargs.get("tool_kwargs"))
    kwargs.pop(TRACE_KEY, None)

    args = summarize_args(kwargs)
    started_at = utc_now_iso()
    started = time.perf_counter()
    response: Any = None
    error: str | None = None

    try:
        response = await _route(
            socket_emitter,
            manager,
            key=key,
            kwargs=kwargs,
            normalize_execute_inputs=normalize_execute_inputs,
            pop_common_execute_kwargs=pop_common_execute_kwargs,
            build_effective_context=build_effective_context,
            apply_depth_guard=apply_depth_guard,
            evaluate_policy=evaluate_policy,
            route_for_key=route_for_key,
            execute_system_minimal=execute_system_minimal,
            execute_invoker_path=execute_invoker_path,
            prepare_pure_prep=prepare_pure_prep,
            execute_pure_path=execute_pure_path,
            collect_param_specs=collect_param_specs,
            extract_cli_overrides=extract_cli_overrides,
            strip_tool_anchor=strip_tool_anchor,
            flag_re=flag_re,
            resolve_param_name=resolve_param_name,
            strip_quotes=strip_quotes,
            coerce_val=coerce_val,
            apply_config_defaults=apply_config_defaults,
            get_cfg_value=get_cfg_value,
            maybe_tool_infer=maybe_tool_infer,
        )
        return response
    except Exception as e:
        error = str(e)
        raise
    finally:
        record_step(
            sink,
            tool=key,
            route=_safe_route(route_for_key, key),
            status=_status_of(response, error),
            started_at=started_at,
            latency_ms=int((time.perf_counter() - started) * 1000),
            args=args,
            result=response,
            error=error,
        )


def _safe_route(route_for_key, key: str) -> str | None:
    """Resolve the route for telemetry only, never raising into the caller.

    ``route_for_key`` rejects a missing or unknown ``meta.route``, and the call
    site is the ``finally`` of the execution - a raise there would replace a
    successful tool result or mask the exception on its way out. An unrecorded
    route is the cheaper loss.
    """
    try:
        return route_for_key(key)
    except Exception:
        return None


def _status_of(response: Any, error: str | None) -> str:
    """Read the outcome off what the call returned.

    Gates that close early label their own envelope via ``error_envelope``, so
    ``vetoed`` and ``blocked`` arrive on the response rather than through a
    side-channel.
    """
    if error is not None:
        return STATUS_ERROR
    if not isinstance(response, dict):
        return STATUS_OK
    if response.get("success") is False:
        kind = response.get("error_kind")
        return kind if isinstance(kind, str) else STATUS_ERROR
    return STATUS_OK


async def _route(
    socket_emitter: SocketEventEmitter,
    manager: Any,
    *,
    key: str,
    kwargs: dict,
    normalize_execute_inputs,
    pop_common_execute_kwargs,
    build_effective_context,
    apply_depth_guard,
    evaluate_policy,
    route_for_key,
    execute_system_minimal,
    execute_invoker_path,
    prepare_pure_prep,
    execute_pure_path,
    collect_param_specs,
    extract_cli_overrides,
    strip_tool_anchor,
    flag_re,
    resolve_param_name,
    strip_quotes,
    coerce_val,
    apply_config_defaults,
    get_cfg_value,
    maybe_tool_infer,
):
    # ---- normalize first ---------------------------------------------------------
    key, kwargs = normalize_execute_inputs(manager, key, kwargs)

    raw_text = (kwargs.get("raw_text") or kwargs.get("raw_request")) or kwargs.get("text") or ""

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🚀 EXECUTE] router_start",
        extra={
            "tool_key": key,
            "has_raw_text": bool(raw_text),
            "kwarg_keys": list(kwargs.keys()),
            "agent_id": getattr(manager.agent, "id", None),
            "agent_name": getattr(manager.agent, "name", None),
        },
    )

    # ---- pop common --------------------------------------------------------------
    common, kwargs = pop_common_execute_kwargs(kwargs)

    factory = manager._factories.get(key, {}) or {}
    cfg_obj = factory.get("config")
    meta = (factory.get("meta") or {}) if factory else {}

    base_text = common["text"] or ""
    text_for_parsing = raw_text or base_text

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🧾 INPUT] router_input_ready",
        extra={
            "tool_key": key,
            "raw_mode": bool(raw_text),
            "text_len": len(text_for_parsing),
        },
    )

    text, effective_ctx, _base_ctx = build_effective_context(
        manager,
        text=text_for_parsing,
        history=common["history"],
        file=common["file"],
        extra=common["extra"],
        kwargs=kwargs,
    )

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🧠 CTX] effective_context_ready",
        extra={
            "tool_key": key,
            "context_keys": list(effective_ctx.keys()),
        },
    )
    effective_ctx["_raw_text"] = text_for_parsing

    err = apply_depth_guard(effective_ctx, key=key)
    if err:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🛑 GUARD] depth_guard_blocked",
            extra={
                "tool_key": key,
                "agent_id": getattr(manager.agent, "id", None),
                "agent_name": getattr(manager.agent, "name", None),
            },
        )
        return err

    # ---- invocation-scope gate ---------------------------------------------------
    # Asked here and only here: workflow-scoped tools are constructible and runnable
    # by the engine, they just aren't a direct-invocation surface.
    scope_veto = evaluate_invocation_scope(manager.agent, manager._factories, key, effective_ctx)
    if scope_veto:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🚫 SCOPE] invocation_scope_veto",
            extra={
                "tool_key": key,
                "reason": scope_veto,
                "agent_id": getattr(manager.agent, "id", None),
                "workflow_depth": effective_ctx.get("workflow_depth"),
            },
        )
        return error_envelope(scope_veto, kind=STATUS_VETOED)

    # ---- governance gate ---------------------------------------------------------
    veto_reason = evaluate_policy(
        manager.agent,
        manager._factories,
        key,
        context=effective_ctx,
    )
    if veto_reason:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🚫 POLICY] policy_veto",
            extra={
                "tool_key": key,
                "reason": veto_reason,
                "agent_id": getattr(manager.agent, "id", None),
                "agent_name": getattr(manager.agent, "name", None),
                "context_keys": list(effective_ctx.keys()),
                "workflow_depth": effective_ctx.get("workflow_depth"),
                "allow_nested_workflows": effective_ctx.get("allow_nested_workflows"),
            },
        )
        return error_envelope(veto_reason, kind=STATUS_VETOED)

    route = route_for_key(key)

    # ---- llm-only ----------------------------------------------------------------
    if route == "llm_only":
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🤖 PATH] llm_only_path",
            extra={"tool_key": key},
        )

        response = await execute_system_minimal(
            socket_emitter,
            manager,
            key=key,
            text=text_for_parsing,
            session_id=common["session_id"],
            request=common["request"],
            policy=common["policy"],
            history=common["history"],
            **kwargs,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🏁 EXECUTE] router_complete",
            extra={
                "tool_key": key,
                "route": route,
            },
        )
        return response

    # ---- invoker -----------------------------------------------------------------
    if route == "invoker":
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🌐 PATH] invoker_path",
            extra={"tool_key": key},
        )

        response = await execute_invoker_path(
            manager,
            key=key,
            meta=meta,
            text=text_for_parsing,
            history=common["history"],
            session_id=common["session_id"],
            policy=common["policy"],
            effective_ctx=effective_ctx,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][🏁 EXECUTE] router_complete",
            extra={
                "tool_key": key,
                "route": route,
            },
        )
        return response

    # ---- pure --------------------------------------------------------------------
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🫧 PATH] pure_path",
        extra={"tool_key": key},
    )

    prep = prepare_pure_prep(
        socket_emitter,
        manager,
        key=key,
        text=text_for_parsing,
        effective_ctx=effective_ctx,
        session_id=common["session_id"],
        request=common["request"],
        policy=common["policy"],
        tool_keys=manager._tool_anchors.get(key, []),
        collect_param_specs=collect_param_specs,
        extract_cli_overrides=extract_cli_overrides,
        strip_tool_anchor=strip_tool_anchor,
        flag_re=flag_re,
        resolve_param_name=resolve_param_name,
        strip_quotes=strip_quotes,
        coerce_val=coerce_val,
    )
    if not prep:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 ROUTER][⚠️ PREP] pure_prep_missing",
            extra={"tool_key": key},
        )
        return error_envelope(f"Tool '{key}' could not be instantiated.")

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🧩 PREP] pure_prep_ready",
        extra={
            "tool_key": key,
            "requires_collection": prep.requires_collection,
            "param_type": type(prep.params).__name__,
        },
    )

    response = await execute_pure_path(
        socket_emitter=socket_emitter,
        manager=manager,
        key=key,
        inst=prep.inst,
        params=prep.params,
        param_specs=prep.param_specs,
        requires_collection=prep.requires_collection,
        clean_text=prep.clean_text,
        flag_overrides=prep.flag_overrides,
        history=common["history"],
        session_id=common["session_id"],
        request=common["request"],
        policy=common["policy"],
        cfg_obj=cfg_obj,
        meta=meta,
        ctx=prep.ctx,
        kwargs=kwargs,
        apply_config_defaults=apply_config_defaults,
        get_cfg_value=get_cfg_value,
        coerce_val=coerce_val,
        maybe_tool_infer=maybe_tool_infer,
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 ROUTER][🏁 EXECUTE] router_complete",
        extra={
            "tool_key": key,
            "route": route,
        },
    )
    return response
