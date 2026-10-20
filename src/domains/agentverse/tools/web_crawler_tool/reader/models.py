from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(slots=True)
class ReportDownload:
    """A downloadable report whose body can be streamed to the client."""

    filename: str
    media_type: str
    body: AsyncIterator[bytes]

