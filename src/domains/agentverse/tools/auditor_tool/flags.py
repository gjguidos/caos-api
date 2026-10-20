from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from src.domains.agentverse.tools.auditor_tool.config import AuditorToolConfig
from src.domains.agentverse.tools.auditor_tool.constants import PATTERN_AUDIT, TEXT_KEY
from src.domains.agentverse.utils.coercion import as_clean_str as _as_clean_str
from src.domains.agentverse.utils.coercion import coerce_bool as _coerce_bool
from src.domains.agentverse.utils.coercion import coerce_int as _coerce_int
from src.domains.agentverse.utils.formatting import as_list as _as_list
from src.domains.agentverse.utils.formatting import as_lower_list as _as_lower_list

# If you want, you can also add TRIGGERS_AUDIT/SHORT_TO_LONG/BOOL_KEYS/INT_KEYS/LIST_KEYS
# usage here later - for now we keep this file focused.


class ParsedAuditFlags(TypedDict, total=False):
    text: str
    domain: str | None
    pack_ids: list[str]
    strict: bool | None
    max_issues: int | None


# Remove a leading @handle or similar noise
_HANDLE_GARBAGE_RE = re.compile(r"^\s*@\w+\s*", re.I)


def _clean_text_to_audit(s: str) -> str:
    """
    Clean the raw text coming from CLI flags or inline triggers.

    - If an @audit-like trigger appears, drop everything up to and including
      the first trigger occurrence.
    - Strip a leading @handle if present.
    - Collapse whitespace.
    """
    if not s:
        return ""

    # Drop everything up to the first @audit / @check / etc. trigger
    m = PATTERN_AUDIT.search(s)
    if m:
        s = s[m.end() :]

    # Strip leading handle if any leftover
    s = _HANDLE_GARBAGE_RE.sub("", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_text(
    raw_text_arg: str,
    flags: Mapping[str, Any],
    *,
    fallback_text: str,
) -> str:
    """
    Decide which text we use as the 'thing to audit':

    Priority:
    1. explicit `text` argument (raw_text_arg)
    2. TEXT_KEY / "text" from flags
    3. fallback_text (typically the raw user message)
    """
    if raw_text_arg:
        return _clean_text_to_audit(raw_text_arg)

    flag_text = _as_clean_str(flags.get(TEXT_KEY) or flags.get("text"))
    if flag_text:
        return _clean_text_to_audit(flag_text)

    return _clean_text_to_audit(fallback_text or "")


def ensure_pack_ids_default(
    pack_ids: Sequence[str],
    *,
    agent_pack_ids: Sequence[str],
) -> list[str]:
    """
    If no pack_ids are explicitly provided, fall back to the agent's rule_pack_ids.

    This lets the auditor tool work out-of-the-box with whatever rule packs
    the role has attached.
    """
    if pack_ids:
        return list(pack_ids)
    return list(agent_pack_ids or [])


def parse_flags(
    flags: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    cfg: AuditorToolConfig,
    text: str,
    *,
    as_list=_as_list,
    as_lower_list=_as_lower_list,
) -> ParsedAuditFlags:
    """
    Basic flag parsing for AuditorTool.

    Supported "flags" (whether from CLI or inline parsing):
      - text / TEXT_KEY: the text to audit
      - domain: e.g. "green_claims", "gdpr"
      - pack_ids: list of rule pack ids
      - strict: bool (if you want stricter behaviour)
      - max_issues: optional int limit (for future use)
    """

    def _get(key: str, default: Any = None) -> Any:
        # flags take precedence over kwargs; fall back to default
        return flags.get(key, kwargs.get(key, default))

    # domain: default to config.default_domain if nothing explicit given
    domain_raw = _as_clean_str(_get("domain", getattr(cfg, "default_domain", None)))
    domain = domain_raw or getattr(cfg, "default_domain", None)

    # strict: default to cfg.strict_default (if present) or False
    strict_val = _get("strict", getattr(cfg, "strict_default", False))
    strict = _coerce_bool(strict_val, default=getattr(cfg, "strict_default", False))

    # max_issues: optional, for later use
    max_issues = _coerce_int(_get("max_issues", None), None)

    # pack_ids: can come as list or space-separated string
    pack_ids_raw = as_list(_get("pack_ids", []))
    # guard against boolean leaks
    pack_ids_raw = [p for p in pack_ids_raw if not isinstance(p, bool)]
    # leave case as-is; your registries likely canonicalise anyway
    pack_ids = [str(p).strip() for p in pack_ids_raw if str(p).strip()]

    # text: prefer explicit arg/flag, then fallback to full message text
    effective_text = _resolve_text(
        raw_text_arg=text,
        flags=flags,
        fallback_text=text,
    )

    return {
        "text": effective_text,
        "domain": domain or None,
        "pack_ids": pack_ids,
        "strict": strict,
        "max_issues": max_issues,
    }
