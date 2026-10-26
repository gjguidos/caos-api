"""Read-only per-record review manifest. This module cannot apply migrations."""

import hashlib
import json
from collections import Counter
from typing import Any

from src.domains.agentverse.entities.resource_ownership import ownership_status
from src.domains.agentverse.security.resource_references import inventory_references

POLICY_FIELDS = ("ownership", "tenant_id", "tenant_scoped", "exposure")
REVISION_FIELDS = (
    "_id",
    "id",
    "key",
    "updated_at",
    "modified_at",
    "modified",
    "created",
    "revision",
    "version",
)


def record_hash(row: dict[str, Any]) -> str:
    """Hash canonical JSON from an authorized export; retain that export privately."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def review_manifest(rows: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    """Preserve exact preconditions; observations never establish ownership.

    Proposal/reviewer/evidence remain empty until an authorized human review.
    Missing/duplicate application identifiers block a future key-based migration.
    _id and the complete row hash must also match in any separately built writer.
    """
    identifiers = [row.get("key") or row.get("id") for row in rows]
    counts = Counter(json.dumps(key, sort_keys=True) for key in identifiers)
    result = []
    for row, key in zip(rows, identifiers, strict=True):
        policy = row.get("metadata", {}) if source.startswith("registry:") else row
        if not isinstance(policy, dict):
            policy = {}
        refs = inventory_references(policy)
        if source.endswith("coll_agents") and row.get("type"):
            refs.insert(0, {"kind": "agent", "key": row["type"]})
        blockers = []
        if not isinstance(key, str) or not key.strip():
            blockers.append("missing_identifier")
        if counts[json.dumps(key, sort_keys=True)] > 1:
            blockers.append("duplicate_identifier")
        result.append(
            {
                "source": source,
                "key": key,
                "record_sha256": record_hash(row),
                "expected_identity_revision": {
                    field: {"present": field in row, "value": row.get(field)}
                    for field in REVISION_FIELDS
                },
                "expected_policy": {
                    field: {"present": field in policy, "value": policy.get(field)}
                    for field in POLICY_FIELDS
                },
                "status": ownership_status(policy),
                "references": refs,
                "observations": {
                    field: row[field]
                    for field in ("genesis_local_seed", "runtime_version", "origin")
                    if field in row
                },
                "blockers": blockers,
                "decision": "review_required",
                "proposed_ownership": None,
                "proposed_tenant_id": None,
                "authority_evidence": [],
                "reviewer": None,
                "rationale": None,
                "ticket": None,
                "apply_authorized": False,
            }
        )
    return result
