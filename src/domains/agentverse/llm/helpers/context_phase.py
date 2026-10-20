from __future__ import annotations

import logging
import re
from typing import Any

from src.domains.agentverse.registries import tool_registry_instance as tool_registry

from .datatypes import ContextDelta
from .protocols import ToolLike, ToolManagerLike

logger = logging.getLogger("llm invoker")


_TOOL_HEAD_RE = re.compile(r"^@[\w\-_]+\b", re.I)


def _is_bare_tool_command(text: str | None) -> bool:
    s = (text or "").strip()
    if not s.startswith("@"):
        return False

    head = _TOOL_HEAD_RE.match(s)
    if not head:
        return False

    rest = s[head.end() :].strip()
    if not rest:
        return True

    # If explicit text/prompt payload exists, definitely not bare
    if "--text=" in rest or "--prompt=" in rest:
        return False

    # Remove flag tokens and their simple values, then see if meaningful payload remains
    tokens = rest.split()
    non_flag_tokens: list[str] = []

    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue

        if tok.startswith("--"):
            # handle forms like: --mode pirate
            if "=" not in tok:
                skip_next = True
            continue

        non_flag_tokens.append(tok)

    return len(non_flag_tokens) == 0


def _msg_role_content(m: Any) -> tuple[str | None, str | None]:
    if isinstance(m, dict):
        return m.get("role"), m.get("content")
    return getattr(m, "role", None), getattr(m, "content", None)


def _last_meaningful_text(history: list[Any] | None) -> str:
    """
    Prefer the most recent assistant output; fallback to a non-command user text.
    """
    for m in reversed(history or []):
        role, content = _msg_role_content(m)
        if not isinstance(content, str):
            continue
        s = content.strip()
        if not s:
            continue
        if role == "assistant":
            return s
        if role == "user" and not s.startswith("@"):
            return s
    return ""


def is_non_system_tool(key: str) -> bool:
    meta = tool_registry.get_metadata_for_ref(key)
    return "system" not in set(meta.get("tags") or [])


def is_pure_tool(key: str) -> bool:
    m = tool_registry.get_metadata_for_ref(key)
    return bool(m.get("execution_mode") == "pure" or m.get("llm_free"))


def as_dict_like(d: Any) -> dict:
    if d is None:
        return {}
    if isinstance(d, dict):
        return d
    for attr in ("model_dump", "dict"):
        fn = getattr(d, attr, None)
        if callable(fn):
            try:
                return fn(exclude_none=True) if attr == "model_dump" else fn()
            except Exception:
                pass
    try:
        from dataclasses import asdict as dc_asdict
        from dataclasses import is_dataclass

        if is_dataclass(d):
            return dc_asdict(d)
    except Exception:
        pass
    return {k: v for k, v in getattr(d, "__dict__", {}).items() if not k.startswith("_")}


def factory_config(tm: ToolManagerLike, key: str) -> dict[str, Any]:
    try:
        cfg = (tm._factories.get(key) or {}).get("config")
        if hasattr(cfg, "model_dump"):
            return cfg.model_dump(exclude_none=True)
        if isinstance(cfg, dict):
            return dict(cfg)
        if hasattr(cfg, "__dict__"):
            return {k: v for k, v in vars(cfg).items() if v is not None}
    except Exception:
        pass
    return {}


def merge_deltas(deltas: list[dict[str, Any]]) -> ContextDelta:
    merged = ContextDelta()
    for d in deltas or []:
        if not isinstance(d, dict):
            continue
        merged.system_overrides += list(d.get("system_overrides") or [])
        merged.messages += list(d.get("messages") or [])
        merged.attachments += list(d.get("attachments") or [])
        merged.evidence += list(d.get("evidence") or [])
        for k, v in (d.get("hints") or {}).items():
            if v is not None:
                merged.hints[k] = v
        if d.get("user_overrides"):
            merged.user_overrides = d.get("user_overrides")
        if isinstance(d.get("post"), list):
            merged.post += [p for p in d["post"] if isinstance(p, dict)]
    return merged


async def collect_context_deltas(
    *,
    tool_keys: list[str],
    text: str,
    session_id: str | None,
    policy: dict[str, Any] | None,
    cli_flags: dict[str, Any],
    args: dict[str, Any],
    tool_manager: ToolManagerLike,
    history: list[dict[str, str]] | None = None,
    context: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> tuple[ContextDelta, list[ToolLike]]:
    deltas: list[dict[str, Any]] = []
    used_tools: list[ToolLike] = []

    # Shared tool-to-tool context (accumulator)
    shared_ctx: dict[str, Any] = {}

    # Effective text that can evolve as tools publish briefs
    effective_text = text
    history_brief = _last_meaningful_text(history)

    if _is_bare_tool_command(text) and history_brief:
        shared_ctx["brief"] = history_brief
        effective_text = history_brief

    for key in tool_keys:
        try:
            tool = tool_manager.ensure_instance(key)
            if not tool or not hasattr(tool, "provide_context"):
                continue

            # Per-tool overrides (args may contain { "<toolkey>": {...} })
            per_tool_args = {}
            if isinstance(args, dict):
                per_tool_args = as_dict_like(args.get(key) or {})

            # Global args that every tool can see (optional)
            global_args = as_dict_like(args or {}) if isinstance(args, dict) else {}

            # Inject shared_ctx so downstream tools can read upstream outputs
            injected_args = dict(global_args)
            injected_args["ctx"] = shared_ctx
            d = await tool.provide_context(
                text=effective_text,
                session_id=session_id,
                policy=policy,
                cli_flags=cli_flags,
                args=injected_args,  # <- tools can read args["ctx"]
                history=history,
                context=context,
                request=request,
                **per_tool_args,  # <- per-tool kwargs
            )
            d = as_dict_like(d)
            if d:
                # Let tools publish ctx updates (e.g. {"brief": "..."} )
                ctx_update = as_dict_like(d.get("ctx_update") or {})
                if ctx_update:
                    shared_ctx.update(ctx_update)

                    # Optional convention: if a tool publishes a brief, it becomes input for later tools
                    brief = ctx_update.get("brief")
                    if isinstance(brief, str) and brief.strip():
                        shared_ctx["brief"] = brief.strip()
                        effective_text = brief.strip()

                # Apply CLI flags over hints (your existing behavior)
                h = as_dict_like(d.get("hints") or {})
                for k in ("style", "size", "quality", "model"):
                    if cli_flags.get(k) is not None:
                        h[k] = cli_flags[k]
                d["hints"] = h

                # (Optional) expose shared ctx downstream in merged result if you want
                # d["ctx_snapshot"] = dict(shared_ctx)

                deltas.append(d)

            used_tools.append(tool)

        except Exception:
            logger.exception("Context phase failed for %s", key)

    merged = merge_deltas(deltas)

    # If you want the invoker to also know the final brief, put it into hints or evidence.
    # This is optional but handy for debugging / orchestration visibility:
    if shared_ctx.get("brief"):
        merged.hints = dict(getattr(merged, "hints", {}) or {})
        merged.hints["brief"] = shared_ctx["brief"]

    return merged, used_tools
