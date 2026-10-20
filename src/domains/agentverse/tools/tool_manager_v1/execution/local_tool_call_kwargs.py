# src/domains/agentverse/tools/tool_manager_v1/execution/pure_kwargs.py
from __future__ import annotations

from typing import Any


async def build_local_tool_call_kwargs(
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
    apply_config_defaults,
    get_cfg_value,
    coerce_val,
    maybe_tool_infer,
) -> dict[str, Any]:
    out_kwargs: dict[str, Any] = {}

    if "request" in params and "request" not in kwargs and request is not None:
        out_kwargs["request"] = request
    if "session_id" in params and "session_id" not in kwargs and session_id is not None:
        out_kwargs["session_id"] = session_id
    if "policy" in params and "policy" not in kwargs and policy is not None:
        out_kwargs["policy"] = policy
    if "agent" in params and "agent" not in kwargs:
        out_kwargs["agent"] = manager.agent
    if "context" in params and "context" not in kwargs:
        out_kwargs["context"] = ctx
    if "ctx" in params and "ctx" not in kwargs:
        out_kwargs["ctx"] = ctx
    if "file" in params and ctx.get("file") is not None and "file" not in kwargs:
        out_kwargs["file"] = ctx["file"]
    if "user_id" in params and "user_id" not in kwargs:
        out_kwargs["user_id"] = ctx.get("user_id")
    if "agent_id" in params and "agent_id" not in kwargs:
        out_kwargs["agent_id"] = getattr(manager.agent, "id", None)
    if "agent_context" in params and "agent_context" not in kwargs:
        ac = getattr(manager.agent, "build_prompt_with_context", None)
        out_kwargs["agent_context"] = (
            ac() if callable(ac) else getattr(manager.agent, "personality_context", None)
        )

    if key == "rag" and "metadata_filters" in params:
        agent_id = getattr(manager.agent, "id", None)
        if agent_id:
            existing_filters = (
                kwargs.get("metadata_filters") or out_kwargs.get("metadata_filters") or {}
            )
            out_kwargs["metadata_filters"] = {**existing_filters, "agent_id": agent_id}

    if requires_collection and "collection" in params and "collection" not in kwargs:
        tool_key = getattr(inst, "key", None)
        if tool_key == "rag":
            coll = None
            try:
                coll = await manager.agent.recall("active_collection")
            except Exception:
                pass
            if not coll:
                cols = getattr(manager.agent, "collections", []) or []
                coll = cols[0] if cols else None
            if coll:
                out_kwargs["collection"] = coll

    if "history" in params and history is not None:
        out_kwargs["history"] = history

    out_kwargs.update(
        apply_config_defaults(
            cfg_obj,
            param_specs=param_specs,
            params=params,
            meta=meta,
            get_cfg_value=get_cfg_value,
            coerce_val=coerce_val,
        )
    )

    inferred = maybe_tool_infer(inst, clean_text, ctx)
    for k, v in (inferred or {}).items():
        if k in params and k not in kwargs:
            out_kwargs[k] = v

    effective_text = ctx.get("_effective_text") or ctx.get("text") or clean_text
    raw_text = ctx.get("_raw_text") or request or ""
    parsed_flags = dict(flag_overrides or {})

    if effective_text and "text" not in parsed_flags:
        parsed_flags["text"] = effective_text

    if raw_text and "raw_text" not in parsed_flags:
        parsed_flags["raw_text"] = raw_text

    if "prompt" in params and "prompt" not in kwargs and effective_text:
        out_kwargs["prompt"] = effective_text
    elif "query" in params and "query" not in kwargs and effective_text:
        out_kwargs["query"] = effective_text
    elif "text" in params and "text" not in kwargs and effective_text:
        out_kwargs["text"] = effective_text

    if "raw_text" in params and "raw_text" not in kwargs:
        out_kwargs["raw_text"] = raw_text

    if "cli_flags" in params and "cli_flags" not in kwargs:
        out_kwargs["cli_flags"] = parsed_flags

    if "flags" in params and "flags" not in kwargs:
        out_kwargs["flags"] = parsed_flags

    if "kwargs" in params and "kwargs" not in kwargs:
        out_kwargs["kwargs"] = parsed_flags

    for k, v in parsed_flags.items():
        if k in params and k not in kwargs:
            out_kwargs[k] = v

    for k, v in kwargs.items():
        if k in params:
            out_kwargs[k] = v

    return out_kwargs
