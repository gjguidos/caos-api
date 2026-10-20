from dataclasses import dataclass, field
from typing import Any


@dataclass
class CrawlAcquisitionResult:
    planned: int
    fetched: int
    queued_for_parse: int
    failed: int
    chunks_completed: int | None
    report_refs: dict[str, dict[str, Any]] = field(
        default_factory=dict,
    )
