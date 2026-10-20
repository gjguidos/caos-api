from __future__ import annotations

import re
from enum import Enum

# main semantic keys used by writer parsing / config mapping
TEXT_KEY = "text"  # user input text (can be topic, idea, or source material)
MODE_KEY = "mode"  # writer mode: write|rewrite|summarize|expand|...

# -------- CLI parsing helpers --------
# maps short flags to long config keys for your generic CLI flag parser

SHORT_TO_LONG = {
    "-r": "--role",
    "-s": "--style",
    "-m": "--mood",
    "-a": "--audience",
    "-c": "--max-chars-length",
    "-p": "--rule-pack",
}

BOOL_KEYS: tuple[str, ...] = (
    "hard_length_enforcement",
    "respect_agent_voice",
)

INT_KEYS: tuple[str, ...] = (
    "max_char_length",
    "min_words",
    "max_words",
    "min_sentences",
    "max_sentences",
)

LIST_KEYS: tuple[str, ...] = (
    "tone_of_voice",
    "rule_pack",
    "rule_packs",
)

# -------- text-scrubbing helpers --------

GLOBAL_HANDLE_RE = re.compile(r"(^|\s)@[\w\-\_]+", re.I)
# Match 2+ spaces/tabs but NOT newlines, so we preserve line breaks
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


class ToneOfVoice(str, Enum):
    CLEAR = "clear"
    CONFIDENT = "confident"
    CURIOUS = "curious"
    HONEST = "honest"
    HUMBLE = "humble"
    OPTIMISTIC = "optimistic"
    PLAYFUL = "playful"
