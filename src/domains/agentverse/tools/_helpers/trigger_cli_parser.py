from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from re import Pattern
from typing import Any

from src.domains.agentverse.utils.canonicalize import canonicalize
from src.domains.agentverse.utils.strip_quotes import strip_quotes

__all__ = [
    "FLAG_START_RE_DEFAULT",
    "CLI_FLAG_RE_DEFAULT",
    "build_at_key_pattern",
    "strip_leading_at_key",
    "parse_tool_cli_from_text",
    "extract_cli_any",
]

# Finds only the beginning of flags, not the value.
# Examples:
#   --topic
#   --topic=...
#   -t
#   -t ...
FLAG_START_RE_DEFAULT: Pattern[str] = re.compile(
    r"""
    (?<!\S)                                  # start of string or whitespace before flag
    (?P<flag>--[a-zA-Z][\w-]*|-{1,2}[a-zA-Z]) # --topic or -t
    (?=(?:=|\s|$))                           # followed by '=', whitespace, or end
    """,
    re.VERBOSE,
)

CLI_FLAG_RE_DEFAULT = FLAG_START_RE_DEFAULT


def build_at_key_pattern(tool_key: str) -> Pattern[str]:
    """Compile an anchor pattern matching ``@<tool_key>`` (canonical, case-insensitive).

    Tools are selected only by their canonical ``@tool_key`` token. The token is matched
    when prefixed with '@', not inside other words/emails, and not suffix-continued.
    """
    core = canonicalize(tool_key)
    if not core:
        return re.compile(r"$.^")  # matches nothing
    return re.compile(rf"(?<!\w)@{re.escape(core)}(?![\w-])", re.IGNORECASE)


def strip_leading_at_key(s: str, tool_key: str) -> str:
    """Strip the first ``@<tool_key>`` occurrence (and trailing separators) from ``s``.

    Uses a search (not an anchor), so the token may appear anywhere in the string;
    everything before and up to the match is discarded.
    """
    pat = build_at_key_pattern(tool_key)
    m = pat.search(s)
    if not m:
        return s
    s = s[m.end() :]
    # drop a single leading separator (':', '-', en/em dash) if present
    s = re.sub(r"^\s*[:\-–—]+(?=\s|$)\s*", "", s.lstrip())
    return s.lstrip()


def _normalize_cli_key(
    raw_flag: str,
    *,
    short_to_long: Mapping[str, str] | None = None,
) -> str:
    stl = dict(short_to_long or {})
    flag = stl.get(raw_flag, raw_flag)
    return flag.lstrip("-").replace("-", "_")


def _parse_cli_value(
    key: str,
    raw_value: str | None,
    *,
    list_keys: set[str],
    int_keys: set[str],
    bool_keys: set[str],
) -> Any:
    if raw_value is None:
        if key in bool_keys:
            return True
        return None

    value = strip_quotes(raw_value.strip())

    if key in list_keys:
        parts = re.split(r"[,\s]+", value.strip())
        return [p for p in parts if p]

    if key in int_keys:
        try:
            return int(value)
        except Exception:
            return value

    if key in bool_keys:
        return value.lower() not in {"false", "0", "no", "off"}

    return value


def _extract_flag_segments(
    s: str,
    *,
    flag_start_re: Pattern[str],
) -> tuple[list[tuple[str, str | None, tuple[int, int]]], str]:
    """
    Returns:
      - list of (raw_flag, raw_value_or_none, full_consumed_span)
      - original string s is left intact; caller can reconstruct free_text from spans

    Rules:
      --flag            -> value None
      --flag=value      -> value is everything until next flag or end
      --flag value ...  -> value is everything until next flag or end
    """
    matches = list(flag_start_re.finditer(s))
    if not matches:
        return [], s

    out: list[tuple[str, str | None, tuple[int, int]]] = []

    for i, m in enumerate(matches):
        raw_flag = m.group("flag")
        start = m.start()
        after_flag = m.end()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(s)

        chunk = s[after_flag:next_start]
        consumed_end = next_start

        raw_value: str | None = None

        if not chunk:
            raw_value = None
        else:
            if chunk.startswith("="):
                raw_value = chunk[1:].strip()
            else:
                # whitespace-delimited value
                stripped = chunk.strip()
                raw_value = stripped if stripped else None

        out.append((raw_flag, raw_value, (start, consumed_end)))

    return out, s


def parse_tool_cli_from_text(
    *,
    text: str,
    tool_key: str,
    cli_flag_re: Pattern[str] = FLAG_START_RE_DEFAULT,
    short_to_long: Mapping[str, str] | None = None,
    list_keys: Iterable[str] = (),
    int_keys: Iterable[str] = (),
    bool_keys: Iterable[str] = (),
    topic_key: str = "topic",
) -> dict[str, Any]:
    """Parse ``--flag`` cli options out of raw text anchored on ``@<tool_key>``."""
    if not text:
        return {}

    s = strip_leading_at_key(text, tool_key)

    flags: dict[str, Any] = {}
    consumed_spans: list[tuple[int, int]] = []

    normalized_list_keys = {str(k).replace("-", "_") for k in list_keys}
    normalized_int_keys = {str(k).replace("-", "_") for k in int_keys}
    normalized_bool_keys = {str(k).replace("-", "_") for k in bool_keys}

    segments, _ = _extract_flag_segments(s, flag_start_re=cli_flag_re)

    for raw_flag, raw_value, span in segments:
        key = _normalize_cli_key(raw_flag, short_to_long=short_to_long)
        parsed = _parse_cli_value(
            key,
            raw_value,
            list_keys=normalized_list_keys,
            int_keys=normalized_int_keys,
            bool_keys=normalized_bool_keys,
        )

        if parsed is not None:
            flags[key] = parsed
        elif key in normalized_bool_keys:
            flags[key] = True

        consumed_spans.append(span)

    residual: list[str] = []
    last = 0
    for a, b in sorted(consumed_spans):
        residual.append(s[last:a])
        last = b
    residual.append(s[last:])

    free = " ".join(" ".join(residual).split()).strip()

    if topic_key not in flags and free:
        flags[topic_key] = free

    flags["free_text"] = free
    return flags


def extract_cli_any(
    *,
    request: Mapping[str, Any] | None,
    kwargs: Mapping[str, Any],
    text: str,
    tool_key: str,
    cli_flag_re: Pattern[str] = FLAG_START_RE_DEFAULT,
    short_to_long: Mapping[str, str] | None = None,
    list_keys: Iterable[str] = (),
    int_keys: Iterable[str] = (),
    bool_keys: Iterable[str] = (),
    topic_key: str = "topic",
    request_text_fields: Sequence[str] = (
        "raw_text",
        "original_text",
        "original_user_text",
        "user_text",
        "last_user_message",
        "input",
        "query",
        "message",
    ),
    extra_keys: Iterable[str] = (),
) -> dict[str, Any]:
    req_cli: dict[str, Any] = {}
    allowed = (
        set(list_keys)
        | set(int_keys)
        | set(bool_keys)
        | {topic_key, "mode"}
        | {str(k) for k in extra_keys}
    )

    at_key_pat = build_at_key_pattern(tool_key)

    if isinstance(request, Mapping):
        if isinstance(request.get("cli"), Mapping):
            req_cli = dict(request["cli"])
        else:
            for k in request_text_fields:
                v = request.get(k)
                if isinstance(v, str) and at_key_pat.search(v or ""):
                    req_cli = parse_tool_cli_from_text(
                        text=v,
                        tool_key=tool_key,
                        cli_flag_re=cli_flag_re,
                        short_to_long=short_to_long,
                        list_keys=list_keys,
                        int_keys=int_keys,
                        bool_keys=bool_keys,
                        topic_key=topic_key,
                    )
                    break

    text_cli = (
        parse_tool_cli_from_text(
            text=text or "",
            tool_key=tool_key,
            cli_flag_re=cli_flag_re,
            short_to_long=short_to_long,
            list_keys=list_keys,
            int_keys=int_keys,
            bool_keys=bool_keys,
            topic_key=topic_key,
        )
        if text
        else {}
    )

    kw_cli: dict[str, Any] = {}
    for k in allowed:
        if k in kwargs and kwargs[k] not in (None, "", []):
            kw_cli[k] = kwargs[k]

    merged: dict[str, Any] = {}
    for src in (kw_cli, text_cli, req_cli):
        for k, v in (src or {}).items():
            merged[k] = v

    return merged
