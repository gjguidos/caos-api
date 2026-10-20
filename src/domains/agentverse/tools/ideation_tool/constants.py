import re

from src.domains.agentverse.tools._helpers.trigger_cli_parser import build_at_key_pattern

SPACES = re.compile(r"\s+")
GLOBAL_HANDLE_RE = re.compile(r"(^|\s)@[\w\-\_]+", re.I)
TRAILING_PUNCT_RE = re.compile(r"[,\.;:!\?]+$")
# Match 2+ spaces/tabs but NOT newlines, so we preserve line breaks
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
LIST_KEYS = {"platforms", "frameworks", "banned_words", "mentions", "constraints"}
INT_KEYS = {"num_ideas", "max_hashtags_per_platform", "seed"}
BOOL_KEYS = {"pretty", "quiet"}
TOPIC_KEY = "topic"

CLI_FLAG_RE = re.compile(
    r"""
    (?P<flag>--[a-zA-Z][\w-]*|-{1,2}[a-zA-Z])       # --topic or -t
    (?:=|\s+)?                                       # optional = or space
    (?P<value>
        "(?:[^"\\]|\\.)*"                            # "quoted with escapes"
        |'(?:[^'\\]|\\.)*'                           # 'quoted with escapes'
        |[^\s]+                                      # bare token
    )?
""",
    re.VERBOSE,
)

SHORT_TO_LONG = {
    "-t": "--topic",
    "-m": "--mode",
    "-g": "--goal",
    "-a": "--audience",
    "-T": "--tone",
    "-n": "--num-ideas",
    "-p": "--platforms",
    "-b": "--brand",
    "-M": "--mentions",
    "-x": "--banned-words",
    "-r": "--region",
    "-F": "--frameworks",
    "-c": "--creativity",
    "-s": "--seed",
    "-o": "--out",
}
# Anchor used to strip a leading @ideation token from raw text / tool output.
PATTERN_IDEATE = build_at_key_pattern("ideation")
