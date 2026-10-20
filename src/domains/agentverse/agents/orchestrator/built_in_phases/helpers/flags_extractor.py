from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools._helpers.trigger_cli_parser import extract_cli_any


def _safe_dict(v: Any) -> dict[str, Any]:
    return dict(v) if isinstance(v, Mapping) else {}


def _tool_factory_meta(tm: Any, ref: str) -> dict[str, Any]:
    factory = (getattr(tm, "_factories", {}) or {}).get(ref) or {}
    return (factory.get("meta") or {}) if isinstance(factory, dict) else {}


def _tool_instance(tm: Any, ref: str) -> Any | None:
    if not (tm and hasattr(tm, "ensure_instance")):
        return None
    try:
        return tm.ensure_instance(ref)
    except Exception:
        return None


def _build_short_to_long_from_cli(meta: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    cli = meta.get("cli") or {}
    for arg in cli.get("args") or []:
        name = str((arg or {}).get("name") or "").strip()
        if not name:
            continue
        parts = [p.strip() for p in name.split(",") if p.strip()]
        long_flag = next((p for p in parts if p.startswith("--")), None)
        if not long_flag:
            continue
        for p in parts:
            if p.startswith("-") and not p.startswith("--"):
                out[p] = long_flag
    return out


def _build_key_buckets(
    meta: Mapping[str, Any], tool: Any | None
) -> tuple[set[str], set[str], set[str], set[str]]:
    list_keys: set[str] = set()
    int_keys: set[str] = set()
    bool_keys: set[str] = set()
    extra_keys: set[str] = set()

    parameters = getattr(tool, "parameters", {}) or {}
    for key, spec in parameters.items():
        k = str(key).replace("-", "_")
        extra_keys.add(k)
        typ = str((spec or {}).get("type") or "").lower()
        if typ in {"array", "list"}:
            list_keys.add(k)
        elif typ in {"int", "integer", "number"}:
            int_keys.add(k)
        elif typ in {"bool", "boolean"}:
            bool_keys.add(k)

    cli = meta.get("cli") or {}
    for arg in cli.get("args") or []:
        name = str((arg or {}).get("name") or "").strip()
        if not name:
            continue
        parts = [p.strip() for p in name.split(",") if p.strip()]
        long_flag = next((p for p in parts if p.startswith("--")), None)
        if not long_flag:
            continue

        norm = long_flag.lstrip("-").replace("-", "_")
        extra_keys.add(norm)

        help_text = str((arg or {}).get("help") or "").lower()
        if "boolean" in help_text or "true" in help_text or "false" in help_text:
            bool_keys.add(norm)

    return list_keys, int_keys, bool_keys, extra_keys


def _apply_cli_alias_normalization(parsed: dict[str, Any]) -> dict[str, Any]:
    if "max_chars" in parsed and "max_char_length" not in parsed:
        parsed["max_char_length"] = parsed["max_chars"]
    return parsed


def _parse_tool_flags(tm: Any, *, key: str, text: str) -> dict[str, Any]:
    if not text:
        return {}

    meta = _tool_factory_meta(tm, key)
    tool = _tool_instance(tm, key)

    short_to_long = _build_short_to_long_from_cli(meta)
    list_keys, int_keys, bool_keys, extra_keys = _build_key_buckets(meta, tool)

    parsed = extract_cli_any(
        request=None,
        kwargs={},
        text=text,
        tool_key=key,
        short_to_long=short_to_long,
        list_keys=list_keys,
        int_keys=int_keys,
        bool_keys=bool_keys,
        topic_key="text",
        extra_keys=extra_keys,
    )

    parsed = _safe_dict(parsed)
    parsed = _apply_cli_alias_normalization(parsed)

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🔍 ANALYZE][🛠️ TOOL_KEY] parsed_tool_flags",
        extra={
            "tool_key": key,
            "parsed_flags": parsed,
            "short_to_long": short_to_long,
            "list_keys": sorted(list(list_keys)),
            "int_keys": sorted(list(int_keys)),
            "bool_keys": sorted(list(bool_keys)),
            "extra_keys": sorted(list(extra_keys)),
        },
    )

    return parsed


def _infer_length_hint(parsed_flags: Mapping[str, Any]) -> str:
    max_chars = parsed_flags.get("max_char_length") or parsed_flags.get("max_chars")
    max_words = parsed_flags.get("max_words")
    max_sentences = parsed_flags.get("max_sentences")

    try:
        if max_chars is not None:
            n = int(max_chars)
            if n >= 2000:
                return "long"
            if n >= 700:
                return "medium"
            return "short"
    except Exception:
        pass

    try:
        if max_words is not None:
            n = int(max_words)
            if n >= 400:
                return "long"
            if n >= 120:
                return "medium"
            return "short"
    except Exception:
        pass

    try:
        if max_sentences is not None:
            n = int(max_sentences)
            if n >= 12:
                return "long"
            if n >= 5:
                return "medium"
            return "short"
    except Exception:
        pass

    return "short"


def _constraints_from_flags(parsed_flags: Mapping[str, Any]) -> list[str]:
    constraints: list[str] = []

    aliases = [
        (("max_char_length", "max_chars"), "max_chars"),
        (("min_words",), "min_words"),
        (("max_words",), "max_words"),
        (("min_sentences",), "min_sentences"),
        (("max_sentences",), "max_sentences"),
        (("hard_length_enforcement",), "hard_length_enforcement"),
        (("respect_agent_voice",), "respect_agent_voice"),
    ]

    for keys, out_key in aliases:
        val = None
        for k in keys:
            if parsed_flags.get(k) not in (None, "", [], {}):
                val = parsed_flags.get(k)
                break
        if val not in (None, "", [], {}):
            constraints.append(f"{out_key}:{val}")

    return constraints


def _tone_from_flags(parsed_flags: Mapping[str, Any]) -> list[str]:
    out: list[str] = []

    for key in ("style", "mood"):
        val = parsed_flags.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())

    tov = parsed_flags.get("tone_of_voice")
    if isinstance(tov, list):
        for item in tov:
            if item is None:
                continue
            s = str(getattr(item, "value", item)).strip()
            if s:
                out.append(s)

    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _audience_from_flags(parsed_flags: Mapping[str, Any]) -> list[str]:
    val = parsed_flags.get("audience")
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    return []


def _brief_from_flags(
    *,
    key: str,
    parsed_flags: Mapping[str, Any],
    qth: float,
) -> dict[str, Any]:
    return {
        "intent": key or "tool",
        "audience": _audience_from_flags(parsed_flags),
        "tone": _tone_from_flags(parsed_flags),
        "constraints": _constraints_from_flags(parsed_flags),
        "length_hint": _infer_length_hint(parsed_flags),
        "acceptance": {
            "must_have": [],
            "prefer": [],
            "quality_threshold": qth,
        },
        "missing": [],
        "risks": [],
    }


def deterministic_brief_from_tool(
    tm: Any,
    *,
    key: str,
    text: str,
    qth: float,
) -> dict[str, Any]:
    """
    Deterministically resolve, for an ``@<tool_key>`` selected tool:
      - parsed flags (anchored on @<tool_key>)
      - a brief derived from those flags
      - routing signals
    """
    parsed_flags = _parse_tool_flags(tm, key=key, text=text)

    brief = _brief_from_flags(
        key=key,
        parsed_flags=parsed_flags,
        qth=qth,
    )

    signals = {
        "routing": {
            "preferred_tools": [key],
            "reason": "@tool_key selection",
        },
        "tool_args": parsed_flags,
    }

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🔍 ANALYZE][🎯 TOOL_KEY] deterministic_brief_resolved",
        extra={
            "tool_key": key,
            "parsed_flags": parsed_flags,
            "brief": brief,
            "quality_threshold": qth,
        },
    )

    return {
        "brief": brief,
        "parsed_flags": parsed_flags,
        "signals": signals,
    }
