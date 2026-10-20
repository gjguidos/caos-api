from __future__ import annotations

import base64
import dataclasses
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

from src.domains.agentverse.utils.strip_quotes import strip_quotes


def json_safe(obj: Any):
    # pydantic v2 / v1
    for m in ("model_dump", "dict"):
        fn = getattr(obj, m, None)
        if callable(fn):
            try:
                return json_safe(fn())
            except Exception:
                pass

    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        try:
            return json_safe(dataclasses.asdict(obj))
        except Exception:
            pass
    if isinstance(obj, set):
        return [json_safe(x) for x in obj]
    if isinstance(obj, Mapping):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, str | bytes | bytearray):
        return [json_safe(x) for x in obj]

    # last resort
    return str(obj)


_FLAG_RE = re.compile(
    r"(?:^|\s)--(?P<key>[A-Za-z0-9_\-]+)"
    r'(?:=(?P<val>"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'|[^\s]+))?'
)


def parse_flags(text: str) -> tuple[str, dict]:
    """Strip CLI-like flags from user text; return (clean_text, flags_dict)."""
    flags, spans = {}, []
    for m in _FLAG_RE.finditer(text or ""):
        key = m.group("key").lower()
        val = strip_quotes(m.group("val"))
        flags[key] = val if val is not None else True
        spans.append(m.span())
    clean = text
    for a, b in sorted(spans, reverse=True):
        clean = clean[:a] + clean[b:]
    return " ".join(clean.split()), flags


def normalize_reply(reply: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply, dict):
        return {"kind": "text", "text": ""}
    if reply.get("kind") == "combined":
        reply.setdefault("text", "")
        reply.setdefault("images", [])
        reply.setdefault("step_texts", {})
    elif reply.get("kind") == "text":
        reply.setdefault("text", "")
    elif reply.get("kind") == "images":
        reply.setdefault("images", [])
    reply.setdefault("addons", {})
    reply.setdefault("meta", {})

    # Interior whitespace is left alone - the client renders markdown, which
    # collapses it anyway, and touching it destroys code indentation and
    # trailing-double-space line breaks. Only the outer padding is cosmetic.
    if isinstance(reply.get("text"), str):
        reply["text"] = reply["text"].strip()

    return reply


def assistant_history_text_from_reply(reply: dict[str, Any]) -> str:
    """
    What to store in memory for assistant turns.
    Keep it simple: prefer text; if no text but images exist, store "[image]".
    """
    if not isinstance(reply, dict):
        return ""
    t = reply.get("text")
    if isinstance(t, str) and t.strip():
        return t
    imgs = reply.get("images")
    if isinstance(imgs, list) and imgs:
        return "[image]"
    return ""
