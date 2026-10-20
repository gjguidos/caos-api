from __future__ import annotations

import base64
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum


def safe_model_dump(obj):
    # normalize ToolResult / pydantic objects to JSON-safe dict
    try:
        # pydantic v2
        return obj.model_dump()
    except Exception:
        pass
    try:
        # pydantic v1
        return obj.dict()
    except Exception:
        pass
    return obj


def json_sanitize(obj):
    # Avoid re-import cycles:
    try:
        from src.domains.agentverse.tools.base import ToolResult
    except Exception:
        ToolResult = None

    if ToolResult and isinstance(obj, ToolResult):
        return {
            "success": bool(getattr(obj, "success", True)),
            "result": json_sanitize(getattr(obj, "result", None)),
            "error": getattr(obj, "error", None),
            "meta": json_sanitize(getattr(obj, "meta", None)),
        }

    # Pydantic v2 / v1
    for m in ("model_dump", "dict"):
        fn = getattr(obj, m, None)
        if callable(fn):
            try:
                return json_sanitize(fn())
            except Exception:
                pass

    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    if isinstance(obj, set):
        return [json_sanitize(x) for x in obj]
    if isinstance(obj, Mapping):
        return {str(k): json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, str | bytes | bytearray):
        return [json_sanitize(x) for x in obj]

    # Fallback: as-is (json will error only if something exotic remains)
    return obj


def strip_tool_anchor(text: str, *, tool_keys: Iterable[str] | None) -> str:
    """
    Remove one leading canonical @tool_key anchor from text.

    Examples:
    - "@hello hello" -> "hello"
    - "@hello" -> ""
    - "hello" -> "hello"

    Behavior:
    - Only strips one leading @tool_key invocation
    - Case-insensitive
    - Leaves payload untouched
    """
    if not text:
        return text

    key_list = [t.strip() for t in (tool_keys or []) if isinstance(t, str) and t.strip()]
    if not key_list:
        return text.strip()

    cleaned = text.strip()

    # longest first to avoid partial matches
    for tool_key in sorted(key_list, key=len, reverse=True):
        pattern = re.compile(rf"^@{re.escape(tool_key)}(?:\s+|$)", re.IGNORECASE)
        if pattern.match(cleaned):
            cleaned = pattern.sub("", cleaned, count=1).strip()
            break

    return cleaned


def maybe_tool_infer(inst, clean_text: str, ctx: dict) -> dict | None:
    infer = getattr(inst, "infer_kwargs", None)
    if callable(infer):
        try:
            return infer(clean_text, ctx) or {}
        except TypeError:
            try:
                return infer(text=clean_text, context=ctx) or {}
            except Exception:
                return {}
        except Exception:
            return {}
    return {}
