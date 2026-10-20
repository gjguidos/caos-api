from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    content_type: str
    encoding: str | None
    body: bytes
    error: str | None = None