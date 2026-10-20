from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Captures:
#   --key=value with spaces until next --other or end
#   --key "value with spaces"
#   --flag
#   --no-flag
CLI_SMART_RE = re.compile(
    r"""
    (?P<full>
        --(?P<key>[a-zA-Z0-9_-]+)
        (?:
            =
            (?P<eq_val>.*?)(?=\s--[a-zA-Z0-9_-]+(?:=|\s|$)|$)
          |
            \s+
            (?P<sp_val>.*?)(?=\s--[a-zA-Z0-9_-]+(?:=|\s|$)|$)
        )?
    )
    """,
    re.VERBOSE,
)


def extract_cli_overrides(
    text: str,
    *,
    param_specs: dict[str, dict[str, Any]],
    flag_re,  # kept for compatibility, not used here
    resolve_param_name: Callable[[str, dict[str, dict[str, Any]]], str | None],
    strip_quotes: Callable[[str], str],
    coerce_val: Callable[[str, dict[str, Any]], Any],
) -> tuple[str, dict[str, Any]]:
    overrides: dict[str, Any] = {}
    spans: list[tuple[int, int]] = []

    for m in CLI_SMART_RE.finditer(text or ""):
        key = (m.group("key") or "").strip()
        raw = m.group("eq_val")
        if raw is None:
            raw = m.group("sp_val")

        key_norm = key.replace("-", "_").lower()

        # --no-flag => False for booleans
        if key_norm.startswith("no_"):
            base = key_norm[3:]
            spec = param_specs.get(base, {}) or {}
            if (spec.get("type", "") or "").lower() in {"bool", "boolean"}:
                overrides[base] = False
                spans.append(m.span("full"))
            continue

        param = resolve_param_name(key_norm, param_specs) or key_norm
        spec = param_specs.get(param, {}) or {}

        if raw is None:
            if (spec.get("type", "") or "").lower() in {"bool", "boolean"}:
                overrides[param] = True
                spans.append(m.span("full"))
            continue

        val = strip_quotes(raw.strip())
        overrides[param] = coerce_val(val, spec)
        spans.append(m.span("full"))

    clean = text or ""
    for a, b in sorted(spans, key=lambda s: s[0], reverse=True):
        clean = clean[:a] + clean[b:]
    clean = " ".join(clean.split())
    return clean, overrides
