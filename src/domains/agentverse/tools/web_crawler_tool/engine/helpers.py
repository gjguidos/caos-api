from __future__ import annotations

from urllib.parse import urldefrag, urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DOCUMENT_EXTENSIONS,
)


def strip_fragment(url: str) -> str:
    clean_url, _fragment = urldefrag(url)
    return clean_url


def is_document_url(url: str) -> bool:
    parsed = urlparse(url or "")
    path = (parsed.path or "").lower()

    return any(
        path.endswith(extension)
        for extension in DOCUMENT_EXTENSIONS
    )