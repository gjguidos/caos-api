from __future__ import annotations

import re
from typing import Any

from src.domains.agentverse.utils.canonicalize import canonicalize

# Matches the first @<tool_key> token (not inside a word/email).
_AT_TOKEN_RE = re.compile(r"(?<!\w)@([A-Za-z0-9][\w-]*)")


def extract_selected_tool(text: str, tm: Any) -> str | None:
    """
    Resolve the tool selected by an ``@<tool_key>`` token in the raw message.

    Selection is canonical-key-only: the token after '@' is canonicalized and must
    match a configured tool key. Aliases are NOT an invocation surface.
    Returns the canonical key, or None when no token matches a configured tool.
    """
    if not text or tm is None:
        return None

    try:
        keys = {canonicalize(k) for k in (tm.list_tool_keys() or [])}
    except Exception:
        keys = set()

    if not keys:
        return None

    for m in _AT_TOKEN_RE.finditer(text):
        token = canonicalize(m.group(1))
        if token in keys:
            return token
    return None


def _first_forced_pure_step_from_plan(tm, steps: list[str]) -> str | None:
    """
    If the plan includes any tool that routes as 'pure', return the first one.
    Uses ToolManager routing (alias-aware).
    """
    if not (tm and hasattr(tm, "_route_for")):
        return None
    for s in steps or []:
        if isinstance(s, str) and s and tm._route_for(s) == "pure":
            return s
    return None


def _forced_pure_from_signals(ctx: dict) -> str | None:
    """
    If Analyze set signals.routing.forced_steps, use it.
    """
    sig = ctx.get("signals") or {}
    routing = (sig.get("routing") or {}) if isinstance(sig, dict) else {}
    forced = routing.get("forced_steps") if isinstance(routing, dict) else None
    if isinstance(forced, list) and forced:
        v = forced[0]
        return v if isinstance(v, str) and v else None
    return None


def first_forced_pure_step(tm, ctx: dict, text: str) -> str | None:
    """
    Resolve 'forced pure' using:
      1) signals.routing.forced_steps (authoritative if present)
      2) plan steps (the selected @tool_key)
    """
    del text  # selection is driven by @tool_key, not free-text scanning
    # 1) from signals (if analyze already decided)
    s = _forced_pure_from_signals(ctx)
    if s:
        return s

    # 2) from plan
    plan_steps = (ctx.get("plan") or {}).get("steps") or []
    return _first_forced_pure_step_from_plan(tm, plan_steps)
