import re

CITATION_BRACKET_RE = re.compile(r"\[(?:Source|Sourced):\s*[^\]]+\]", re.I)


def strip_citations(s: str) -> str:
    if not isinstance(s, str):
        s = str(s or "")
    return CITATION_BRACKET_RE.sub("", s).strip()


def as_list(x) -> list[str]:
    if x is None or isinstance(x, bool):
        return []
    if isinstance(x, list):
        return [str(i).strip() for i in x if str(i).strip()]
    if isinstance(x, str):
        parts = re.split(r"[,\s]+", x.strip())
        return [p for p in parts if p]
    return []


def as_lower_list(x) -> list[str]:
    return [p.lower() for p in as_list(x)]
