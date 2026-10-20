from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from src.domains.agentverse.tools._helpers.trigger_cli_parser import strip_leading_at_key
from src.domains.agentverse.tools.writer_tool.config import WriterToolConfig
from src.domains.agentverse.tools.writer_tool.constants import (
    ToneOfVoice,
)
from src.domains.agentverse.utils.coercion import (
    as_clean_str as _as_clean_str,
)
from src.domains.agentverse.utils.coercion import (
    coerce_bool as _coerce_bool,
)
from src.domains.agentverse.utils.coercion import (
    coerce_enum as _coerce_enum,
)
from src.domains.agentverse.utils.coercion import (
    coerce_int as _coerce_int,
)
from src.domains.agentverse.utils.coercion import (
    coerce_str_list as _coerce_str_list,
)


class ParsedWriterFlags(TypedDict, total=False):
    """
    Normalized, tool-friendly flag bag for WriterTool.
    """

    # main content input
    text: str

    # persona / tone
    role: str | None
    style: str | None
    mood: str | None
    audience: str | None
    tone_of_voice: list[ToneOfVoice] | None

    # behaviour / language
    mode: str | None

    # deterministic analysis helpers
    has_explicit_text_flag: bool

    # length constraints
    max_char_length: int | None
    min_words: int | None
    max_words: int | None
    min_sentences: int | None
    max_sentences: int | None

    # rule packs
    rule_packs: list[str]

    # extra control toggles
    hard_length_enforcement: bool | None
    respect_agent_voice: bool | None


_HANDLE_GARBAGE_RE = re.compile(r"^\s*@\w+\s*", re.I)


def _clean_text_to_write(s: str) -> str:
    """
    Clean raw text or fallback message content for writer use.

    Important:
    - Strips the first @writer occurrence (anywhere in the string) and everything before it.
    - Do NOT destroy normal user content such as '--text foo' values.
    """
    if not s:
        return ""

    s = strip_leading_at_key(s, "writer")
    s = _HANDLE_GARBAGE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_fallback_text(
    raw_text_arg: str,
    flags: Mapping[str, Any],
    *,
    fallback_text: str,
) -> str:
    """
    Choose the best-effort effective text.

    Priority:
    1) explicit flag text
    2) explicit raw_text_arg
    3) fallback_text
    """
    flag_text = _as_clean_str(flags.get("text"))
    if flag_text:
        return flag_text

    if raw_text_arg:
        return _clean_text_to_write(raw_text_arg)

    return _clean_text_to_write(fallback_text or "")


def parse_flags(
    flags: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    cfg: WriterToolConfig,
    text: str,
) -> ParsedWriterFlags:
    """
    Normalize writer flags extracted upstream.

    Precedence:
        flags > kwargs > cfg defaults
    """
    flags = flags or {}
    kwargs = kwargs or {}

    def get(key: str, default: Any = None) -> Any:
        return flags.get(key, kwargs.get(key, default))

    role, style, mood, audience = _make_persona(get, cfg)
    tone_of_voice = _make_tone_of_voice(get)

    explicit_mode = _make_mode(get, cfg)

    (
        max_char_length,
        min_words,
        max_words,
        min_sentences,
        max_sentences,
    ) = _make_length_constraints(get, cfg)

    rule_packs = _make_rules(get)
    hard_length_enforcement, respect_agent_voice = _make_toggles(get, cfg)

    source_text = _make_content(get, raw_text=text, flags=flags)

    # Mode is an opaque pass-through flag: explicit --mode (validated against
    # allowed_modes) or the tool's configured default. No trigger-keyword inference.
    mode = explicit_mode or getattr(cfg, "default_mode", None)
    has_explicit_text_flag = _as_clean_str(flags.get("text")) != ""

    return {
        "text": source_text,
        "mode": mode,
        "has_explicit_text_flag": has_explicit_text_flag,
        "role": role,
        "style": style,
        "mood": mood,
        "audience": audience,
        "tone_of_voice": tone_of_voice,
        "max_char_length": max_char_length,
        "min_words": min_words,
        "max_words": max_words,
        "min_sentences": min_sentences,
        "max_sentences": max_sentences,
        "rule_packs": rule_packs,
        "hard_length_enforcement": hard_length_enforcement,
        "respect_agent_voice": respect_agent_voice,
    }


def _make_persona(
    get: Callable[[str, Any], Any],
    cfg: WriterToolConfig,
) -> tuple[str | None, str | None, str | None, str | None]:
    role_raw = _as_clean_str(get("role", getattr(cfg, "default_role", None)))
    style_raw = _as_clean_str(get("style", getattr(cfg, "default_style", None)))
    mood_raw = _as_clean_str(get("mood", getattr(cfg, "default_mood", None)))
    audience_raw = _as_clean_str(get("audience", getattr(cfg, "default_audience", None)))

    role = role_raw or getattr(cfg, "default_role", None)
    style = style_raw or getattr(cfg, "default_style", None)
    mood = mood_raw or getattr(cfg, "default_mood", None)
    audience = audience_raw or getattr(cfg, "default_audience", None)

    return (
        role or None,
        style or None,
        mood or None,
        audience or None,
    )


def _make_tone_of_voice(get: Callable[[str, Any], Any]) -> list[ToneOfVoice] | None:
    raw = get("tone_of_voice", [])
    items = _coerce_str_list(raw)
    out: list[ToneOfVoice] = []
    for v in items:
        tv = _coerce_enum(v, ToneOfVoice)
        if tv is not None:
            out.append(tv)
    return out or None


def _make_mode(get: Callable[[str, Any], Any], cfg: WriterToolConfig) -> str | None:
    raw = _as_clean_str(get("mode", getattr(cfg, "default_mode", None)))
    if not raw:
        return None

    allowed: Sequence[str] = getattr(cfg, "allowed_modes", []) or []
    if allowed:
        allowed_set = {str(x).strip().lower() for x in allowed}
        if raw.strip().lower() not in allowed_set:
            return getattr(cfg, "default_mode", None)

    return raw.strip().lower() or None


def _make_length_constraints(
    get: Callable[[str, Any], Any],
    cfg: WriterToolConfig,
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    max_char_length = _coerce_int(
        get("max_char_length", getattr(cfg, "max_char_length", None)),
        getattr(cfg, "max_char_length", None),
    )
    min_words = _coerce_int(
        get("min_words", getattr(cfg, "min_words", None)),
        getattr(cfg, "min_words", None),
    )
    max_words = _coerce_int(
        get("max_words", getattr(cfg, "max_words", None)),
        getattr(cfg, "max_words", None),
    )
    min_sentences = _coerce_int(
        get("min_sentences", getattr(cfg, "min_sentences", None)),
        getattr(cfg, "min_sentences", None),
    )
    max_sentences = _coerce_int(
        get("max_sentences", getattr(cfg, "max_sentences", None)),
        getattr(cfg, "max_sentences", None),
    )
    return max_char_length, min_words, max_words, min_sentences, max_sentences


def _make_rules(
    get: Callable[[str, Any], Any],
) -> list[str]:
    raw = get("rule_pack", None)
    if raw is None:
        raw = get("rule_packs", [])
    return [s.lower() for s in _coerce_str_list(raw)]


def _make_toggles(
    get: Callable[[str, Any], Any],
    cfg: WriterToolConfig,
) -> tuple[bool | None, bool | None]:
    hard_length_enforcement = _coerce_bool(
        get("hard_length_enforcement", getattr(cfg, "hard_length_enforcement", True)),
        default=getattr(cfg, "hard_length_enforcement", True),
    )
    respect_agent_voice = _coerce_bool(
        get("respect_agent_voice", getattr(cfg, "respect_agent_voice", True)),
        default=getattr(cfg, "respect_agent_voice", True),
    )
    return hard_length_enforcement, respect_agent_voice


def _make_content(
    get: Callable[[str, Any], Any],
    *,
    raw_text: str,
    flags: Mapping[str, Any],
) -> str:
    text_raw = _as_clean_str(get("text", ""))
    if text_raw:
        return text_raw

    return _resolve_fallback_text(
        raw_text_arg=raw_text,
        flags=flags,
        fallback_text=raw_text,
    )
