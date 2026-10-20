from __future__ import annotations

from urllib.parse import urlparse

INAPPROPRIATE_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "adult": {"porn", "xxx", "sex", "adult", "camgirl", "escort"},
    "gambling": {"casino", "betting", "sportsbook", "poker", "slots"},
    "illegal_drugs": {"cocaine", "heroin", "meth", "lsd", "fentanyl", "weed", "marijuana", "cannabis", "shrooms"},
    "weapons": {"guns", "firearms", "ammo", "ammunition", "explosives"},
    "malware": {"malware", "ransomware"},
    "phishing": {"phishing"},
    "piracy": {"crack", "keygen", "warez"},
    "extremism": {"nazi", "isis", "terror"},
}


INAPPROPRIATE_PATH_KEYWORDS: dict[str, set[str]] = {
    "adult": {"/porn", "/xxx"},
    "gambling": {"/casino", "/betting"},
    "weapons": {"/weapons", "/ammo"},
    "malware": {"/malware"},
    "piracy": {"/crack", "/keygen", "/warez"},
}


def classify_url_by_heuristics(url: str) -> tuple[str | None, str | None]:
    """
    Cheap deterministic pre-filter.

    Returns:
        (category, reason) if suspicious/inappropriate, otherwise (None, None).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    host_parts = set(host.replace("-", ".").split("."))

    for category, keywords in INAPPROPRIATE_DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in host_parts or keyword in host:
                return category, f"Blocked domain keyword: {keyword}"

    for category, keywords in INAPPROPRIATE_PATH_KEYWORDS.items():
        for keyword in keywords:
            if keyword in path:
                return category, f"Blocked path keyword: {keyword}"

    return None, None