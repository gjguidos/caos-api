import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from src.domains.agentverse.tools.ideation_tool.config import IdeationToolConfig
from src.domains.agentverse.tools.ideation_tool.constants import GLOBAL_HANDLE_RE, PATTERN_IDEATE
from src.domains.agentverse.utils.coercion import (
    as_clean_str as _as_clean_str,
)
from src.domains.agentverse.utils.coercion import (
    coerce_int as _coerce_int,
)
from src.domains.agentverse.utils.formatting import (
    as_list as _as_list,
)
from src.domains.agentverse.utils.formatting import (
    as_lower_list as _as_lower_list,
)


class ParsedFlags(TypedDict, total=False):
    mode: str
    topic: str
    num_ideas: int
    goal: str | None
    audience: str | None
    tone: str | None
    brand: str | None
    region: str | None
    creativity: str | None
    seed: int | None
    frameworks: list[str]
    mentions: list[str]
    banned_words: list[str]
    platforms: list[str]
    constraints: list[str]
    list_style: str
    max_hashtags_per_platform: int | None


_HANDLE_GARBAGE_RE = re.compile(r"^\s*@\w+\s*", re.I)


def _clean_topic_text(s: str) -> str:
    if not s:
        return ""
    # If a trigger appears, drop everything up to the first trigger occurrence
    m = PATTERN_IDEATE.search(s)
    if m:
        s = s[m.end() :]  # text after "@ideate" token
    # Strip a leading @handle if any leftover
    s = _HANDLE_GARBAGE_RE.sub("", s)
    # Collapse whitespace and trim
    s = re.sub(r"\s+", " ", s).strip()
    # Final safety: remove any stray handles inside the string
    s = GLOBAL_HANDLE_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _coerce_topic(val: Any, fallback_text: str) -> str:
    """
    Topic must be a non-empty string.
    - If val is bool/empty -> use fallback_text.
    - If after cleaning it's empty -> use fallback_text.
    """
    s = _as_clean_str(val)
    if not s:
        s = (fallback_text or "").strip()
    return s


def ensure_platform_defaults(mode: str, platforms: Sequence[str]) -> list[str]:
    if mode == "social" and not platforms:
        return ["x", "instagram", "linkedin"]
    return list(platforms or [])


def parse_flags(
    flags: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    cfg: "IdeationToolConfig",
    text: str,
    *,
    as_list=_as_list,
    as_lower_list=_as_lower_list,
) -> ParsedFlags:
    def _get(key: str, default: Any = None) -> Any:
        # flags take precedence over kwargs; fall back to default
        return flags.get(key, kwargs.get(key, default))

    mode = _as_clean_str(_get("mode", cfg.default_mode)) or cfg.default_mode
    topic = _coerce_topic(_get("topic"), (text or "").strip())
    topic = _clean_topic_text(topic)
    num_ideas = _coerce_int(_get("num_ideas", cfg.default_num_ideas), cfg.default_num_ideas)
    goal = _as_clean_str(_get("goal"))
    audience = _as_clean_str(_get("audience"))
    tone = _as_clean_str(_get("tone"))
    brand = _as_clean_str(_get("brand"))
    region = _as_clean_str(_get("region"))
    creativity = _as_clean_str(_get("creativity"))
    seed = _coerce_int(_get("seed"), None)

    # lists
    frameworks_raw = as_list(_get("frameworks", []))
    # guard against boolean leaks like True/False
    frameworks_raw = [x for x in frameworks_raw if not isinstance(x, bool)]
    frameworks = [
        f.upper() for f in frameworks_raw if f and f.upper() in (cfg.allow_frameworks or [])
    ]

    mentions = [m for m in as_list(_get("mentions", [])) if not isinstance(m, bool)]
    banned = [w for w in as_list(_get("banned_words", [])) if not isinstance(w, bool)]
    platforms = [p for p in as_lower_list(_get("platforms", [])) if not isinstance(p, bool)]
    constraints = [c for c in as_list(_get("constraints", [])) if not isinstance(c, bool)]

    # style + caps (flags override kwargs, then cfg)
    list_style = (
        _as_clean_str(_get("list_style", kwargs.get("list_style", cfg.list_style)))
        or cfg.list_style
    )
    max_tags = _coerce_int(
        _get("max_hashtags_per_platform", kwargs.get("max_hashtags_per_platform")), None
    )

    platforms = ensure_platform_defaults(mode, platforms)

    # clamp num_ideas
    if num_ideas is None:
        num_ideas = cfg.default_num_ideas
    num_ideas = max(1, min(int(num_ideas), 50))

    return {
        "mode": mode,
        "topic": topic,
        "num_ideas": num_ideas,
        "goal": goal or None,
        "audience": audience or None,
        "tone": tone or None,
        "brand": (brand or "").strip() or None,
        "region": region or None,
        "creativity": creativity or None,
        "seed": seed,
        "frameworks": frameworks,
        "mentions": mentions,
        "banned_words": banned,
        "platforms": list(platforms),
        "constraints": constraints,
        "list_style": list_style,
        "max_hashtags_per_platform": max_tags,
    }
