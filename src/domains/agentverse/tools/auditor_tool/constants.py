# src/domains/agentverse/tools/auditor_tool/constants.py
from __future__ import annotations

import re

from src.domains.agentverse.tools._helpers.trigger_cli_parser import build_at_key_pattern

# Anchor used to strip a leading @auditor token from raw text / tool output.
PATTERN_AUDIT = build_at_key_pattern("auditor")

# Key used by trigger_cli_parser + flags for the main text argument
TEXT_KEY = "text"

# -------- CLI parsing helpers (mirrors ideation style) --------

# Map short flags -> long flags if you ever expose this via CLI:
#   @audit -d green_claims --strict "some text"
SHORT_TO_LONG = {
    "d": "domain",
    "p": "pack_ids",
    "s": "strict",
}

# Which parsed keys should be treated as bool/int/list by extract_cli_any
BOOL_KEYS = ("strict",)
INT_KEYS = ()  # nothing numeric for now, you can add e.g. ("max_findings",)
LIST_KEYS = ("pack_ids",)

# -------- text-scrubbing helpers (used by formatter/specs) --------

# Strip global @handles from any output you display
GLOBAL_HANDLE_RE = re.compile(r"(^|\s)@[\w\-\_]+", re.I)

# Collapse multiple spaces/tabs but NOT newlines, so we preserve line breaks
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
