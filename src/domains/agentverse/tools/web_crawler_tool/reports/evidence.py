from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlerEvidenceMetadata,
)


def build_evidence_metadata(
    evidence: dict[str, Any] | None,
) -> CrawlerEvidenceMetadata:
    evidence = evidence or {}
    
    return CrawlerEvidenceMetadata(
        origin=str(
            evidence.get("source")
            or "unknown"
        ),
        reused=bool(
            evidence.get("reused", False)
        ),
        snapshot_id=evidence.get(
            "snapshot_id"
        ),
        snapshot_created_at=evidence.get(
            "snapshot_created_at"
        ),
        snapshot_age_seconds=evidence.get(
            "snapshot_age_seconds"
        ),
        acquisition_fingerprint=evidence.get(
            "acquisition_fingerprint"
        ),
    )