from __future__ import annotations

import json
from typing import Any, TypedDict

from src.domains.agentverse.utils.estimate_tokens import estimate_tokens

from .constants import GLOBAL_HANDLE_RE, MULTI_SPACE_RE


class WriterFormatterCtx(TypedDict, total=False):
    # original input / prompt-ish
    raw_text: str

    # persona / tone
    role: str | None
    style: str | None
    mood: str | None
    audience: str | None

    # behaviour / language
    mode: str | None
    language: str | None

    # length constraints
    max_char_length: int | None
    min_words: int | None
    max_words: int | None
    min_sentences: int | None
    max_sentences: int | None


def _usage_stub(text: str) -> dict:
    out = estimate_tokens(text)
    return {
        "usage": {
            "latency_ms": None,
            "model": None,
            "provider": None,
            "tokens": {"input": 0, "output": out, "total": out},
            "estimated": True,
        }
    }


def _safe_json_dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _scrub_text(text: str) -> str:
    # Strip @handles and collapse whitespace like in ideation/auditor
    text = GLOBAL_HANDLE_RE.sub(" ", text or "")
    text = MULTI_SPACE_RE.sub(" ", text).strip()
    return text


def _extract_main_text(data: Any) -> str:
    """
    Heuristic to extract the main written text from model output.

    Accepts:
    - plain string
    - dict with 'text' or 'answer' keys
    - anything else → JSON-dumped string
    """
    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        # common shapes: {"answer": "..."} or {"text": "..."}
        if isinstance(data.get("text"), str):
            return data["text"]
        if isinstance(data.get("answer"), str):
            return data["answer"]
        # nothing obvious → fallback to JSON
        return _safe_json_dump(data)

    # generic fallback
    return _safe_json_dump(data)


def format_writer_result(
    data: object,
    *,
    ctx: WriterFormatterCtx,
) -> dict[str, Any]:
    """
    Normalize whatever the model produced into a canonical writer JSON:

      {
        "text": "...",         # main output copy
        "meta": { ... },       # role/style/mood/constraints/etc.
        "usage": { ... }       # lightweight usage estimate
      }

    It is tolerant to:
    - Plain strings
    - Dicts with 'text' or 'answer'
    - Arbitrary dicts / objects (wrapped as JSON text)
    """
    raw_text_ctx = ctx.get("raw_text") or ""

    # --- main text ---
    main_text = _extract_main_text(data)
    main_text = _scrub_text(main_text)

    # --- meta: start from ctx, but don't explode on missing keys ---
    meta: dict[str, Any] = {
        "role": ctx.get("role"),
        "style": ctx.get("style"),
        "mood": ctx.get("mood"),
        "audience": ctx.get("audience"),
        "mode": ctx.get("mode"),
        "language": ctx.get("language"),
        "constraints": {
            "max_char_length": ctx.get("max_char_length"),
            "min_words": ctx.get("min_words"),
            "max_words": ctx.get("max_words"),
            "min_sentences": ctx.get("min_sentences"),
            "max_sentences": ctx.get("max_sentences"),
        },
        "raw_text": _scrub_text(raw_text_ctx),
    }

    # optional: if the model already returned some meta, merge it (model wins)
    if isinstance(data, dict):
        model_meta = data.get("meta") or {}
        if isinstance(model_meta, dict):
            # shallow merge: model_meta overrides existing keys
            meta.update(model_meta)

    payload = {
        "text": main_text,
        "meta": meta,
    }

    payload["usage"] = _usage_stub(main_text)
    return payload
