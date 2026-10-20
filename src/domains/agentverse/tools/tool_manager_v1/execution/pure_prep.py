from __future__ import annotations

import logging
from dataclasses import dataclass
from inspect import signature
from typing import Any

from src.domains.agentverse.tools.base import BaseTool

logger = logging.getLogger("agentverse.tools.tool_manager.pure_prep")


@dataclass
class PurePrep:
    inst: Any
    ctx: dict
    effective_text: str
    params: set[str]
    param_specs: dict
    requires_collection: bool
    clean_text: str
    flag_overrides: dict


def resolve_effective_text(*, raw_text: str, clean_text: str, flag_overrides: dict) -> str:
    # 1) explicit CLI override wins
    t = flag_overrides.get("text")
    if isinstance(t, str) and t.strip():
        return t.strip()

    # 2) otherwise cleaned text wins (but avoid lone "@" leftover)
    if isinstance(clean_text, str):
        ct = clean_text.strip()
        if ct and ct != "@":
            return ct

    # 3) fallback to raw
    return (raw_text or "").strip()


def ensure_instance_or_none(manager: Any, key: str):
    inst = manager.ensure_instance(key)
    return inst


def inject_tool_runner_if_needed(
    socket_emitter,
    manager: Any,
    *,
    inst: Any,
    effective_ctx: dict,
    session_id,
    request,
    policy,
):
    if getattr(inst, "requires_tool_runner", False) and "tool_runner" not in effective_ctx:

        async def _tool_runner(child_key: str, params: dict[str, Any] | None = None):
            params = dict(params or {})
            # use the parent's effective text (computed in prepare_pure_prep)
            eff_text = effective_ctx.get("_effective_text") or effective_ctx.get("text") or request
            params.setdefault("session_id", session_id)
            params.setdefault("request", eff_text)  # <-- critical
            params.setdefault("raw_text", eff_text)
            params.setdefault("policy", policy)

            if "history" not in params and effective_ctx.get("history") is not None:
                params["history"] = effective_ctx.get("history")

            # The contract with callers is (ok, tool_env) - a flat tool env, never
            # a wrapper around one. `as_envelope` has already peeled the semantic
            # `result` layer (including a doubled `result.result`), so
            # `envelope["result"]` is that env. It does not touch `data`: a tool's
            # `data` is a real env key that declared output paths bind into, not a
            # wrapper to unwrap.
            envelope = await manager.execute(socket_emitter, child_key, **params)
            if not envelope:
                return False, {"error": f"Tool '{child_key}' returned no envelope"}

            result = envelope.get("result")
            if result is None:
                return False, {
                    "error": envelope.get("error") or f"Tool '{child_key}' returned no result",
                    "success": False,
                }

            return bool(envelope.get("success", True)), result

        effective_ctx["tool_runner"] = _tool_runner


def discover_params_and_specs(
    *,
    inst: Any,
    collect_param_specs,
) -> tuple[set[str], dict, bool]:
    tool_cls = inst.__class__
    subclass_run = getattr(tool_cls, "_run", None)
    has_subclass_run = callable(subclass_run) and subclass_run is not BaseTool._run

    exec_sig = signature(inst.execute)
    param_names = set(exec_sig.parameters.keys())
    if has_subclass_run:
        run_sig = signature(inst._run)
        param_names |= set(run_sig.parameters.keys())

    params = {p for p in param_names if p != "self"}
    requires_collection = bool(getattr(inst, "requires_collection", False))
    param_specs = collect_param_specs(tool_cls)
    return params, param_specs, requires_collection


def parse_cli_and_strip(
    *,
    text: str,
    tool_keys: list[str],
    param_specs: dict,
    extract_cli_overrides,
    strip_tool_anchor,
    flag_re,
    resolve_param_name,
    strip_quotes,
    coerce_val,
) -> tuple[str, dict]:
    clean_text, flag_overrides = extract_cli_overrides(
        text,
        param_specs=param_specs,
        flag_re=flag_re,
        resolve_param_name=resolve_param_name,
        strip_quotes=strip_quotes,
        coerce_val=coerce_val,
    )
    clean_text = strip_tool_anchor(clean_text, tool_keys=tool_keys)
    return clean_text, flag_overrides


def wire_runtime(
    *,
    manager: Any,
    inst: Any,
    request,
    session_id,
):
    # exact copy of your behavior
    if hasattr(inst, "set_runtime"):
        try:
            inst.set_runtime(agent=manager.agent, request=request, session_id=session_id)
        except Exception:
            setattr(inst, "agent", manager.agent)
            if request is not None:
                setattr(inst, "request", request)
            if session_id is not None:
                setattr(inst, "session_id", session_id)
    else:
        setattr(inst, "agent", manager.agent)
        if request is not None:
            setattr(inst, "request", request)
        if session_id is not None:
            setattr(inst, "session_id", session_id)


def prepare_pure_prep(
    socket_emitter,
    manager: Any,
    *,
    key: str,
    text: str,
    effective_ctx: dict,
    session_id,
    request,
    policy,
    tool_keys: list[str],
    collect_param_specs,
    extract_cli_overrides,
    strip_tool_anchor,
    flag_re,
    resolve_param_name,
    strip_quotes,
    coerce_val,
) -> PurePrep | None:
    inst = ensure_instance_or_none(manager, key)
    if not inst:
        return None

    inject_tool_runner_if_needed(
        socket_emitter,
        manager,
        inst=inst,
        effective_ctx=effective_ctx,
        session_id=session_id,
        request=request,
        policy=policy,
    )

    params, param_specs, requires_collection = discover_params_and_specs(
        inst=inst,
        collect_param_specs=collect_param_specs,
    )

    clean_text, flag_overrides = parse_cli_and_strip(
        text=text,
        tool_keys=tool_keys,
        param_specs=param_specs,
        extract_cli_overrides=extract_cli_overrides,
        strip_tool_anchor=strip_tool_anchor,
        flag_re=flag_re,
        resolve_param_name=resolve_param_name,
        strip_quotes=strip_quotes,
        coerce_val=coerce_val,
    )

    raw_text = text
    effective_text = resolve_effective_text(
        raw_text=raw_text,
        clean_text=clean_text,
        flag_overrides=flag_overrides or {},
    )

    # Mutate the ctx we pass into the tool
    effective_ctx["text"] = effective_text
    effective_ctx["_effective_text"] = effective_text
    effective_ctx["_raw_text"] = raw_text
    effective_ctx["_clean_text"] = clean_text
    effective_ctx["_flag_overrides"] = flag_overrides or {}

    wire_runtime(manager=manager, inst=inst, request=request, session_id=session_id)

    return PurePrep(
        inst=inst,
        ctx=effective_ctx,
        params=params,
        param_specs=param_specs,
        requires_collection=requires_collection,
        clean_text=clean_text,
        effective_text=effective_text,
        flag_overrides=flag_overrides,
    )
