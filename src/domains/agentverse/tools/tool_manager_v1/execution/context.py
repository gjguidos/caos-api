from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools._helpers.rule_context import resolve_rule_context
from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY


def pop_common_execute_kwargs(kwargs: dict) -> tuple[dict, dict]:
    """
    Pops the same keys execute() currently pops.
    Returns:
      common: dict with popped values
      rest: remaining kwargs (mutated original in old code; here we return rest)
    """
    # copy to preserve “mutate kwargs” semantics? safest is: operate on the passed dict
    text = kwargs.pop("text", "") or ""
    history = kwargs.pop("history", None)
    file = kwargs.pop("file", None)
    extra = kwargs.pop("extra", None)

    session_id = kwargs.pop("session_id", None)
    request = kwargs.pop("request", None)
    policy = kwargs.pop("policy", None)

    return {
        "text": text,
        "history": history,
        "file": file,
        "extra": extra,
        "session_id": session_id,
        "request": request,
        "policy": policy,
    }, kwargs


def build_effective_context(
    manager: Any, *, text: str, history, file, extra, kwargs: dict
) -> tuple[str, dict, dict]:
    base_ctx = manager._build_context(text=text, history=history, file=file, extra=extra)
    text = base_ctx["text"]

    provided_context = kwargs.get("context")
    if isinstance(provided_context, dict):
        effective_ctx = provided_context
        effective_ctx.setdefault("text", base_ctx.get("text", ""))
        effective_ctx.setdefault("history", base_ctx.get("history", []))
        effective_ctx.setdefault("file", base_ctx.get("file"))
        effective_ctx.setdefault("user_id", base_ctx.get("user_id"))
    else:
        effective_ctx = base_ctx
        kwargs["context"] = effective_ctx

    if files := kwargs.get("files"):
        effective_ctx["files"] = files

    if not effective_ctx.get("rule_context"):
        rule_ctx = resolve_rule_context(
            agent_pack_ids=list(getattr(manager.agent, "rule_pack_ids", []) or []),
        )
        effective_ctx["rule_context"] = {
            "packs": rule_ctx.packs,
            "rules": rule_ctx.rules,
            "selected_pack_refs": rule_ctx.selected_pack_refs,
            "unresolved_pack_refs": rule_ctx.unresolved_pack_refs,
            "domain": rule_ctx.domain,
        }

    # Set unconditionally and overwritten on a nested call: the facts describe the
    # manager currently executing, so a caller-supplied context must never be able
    # to speak for one it doesn't belong to.
    effective_ctx[CONTEXT_KEY] = manager.facts

    return text, effective_ctx, base_ctx
