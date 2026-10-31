"""Read-only inspection of an Agent's whole definition history (REV-5).

Extends the REV-1 baseline verifier to governed revisions: creation baseline, immutable
chain, selected successor, applied projection, incomplete applications and missing
Seshat receipts. Pure: callers pass already-read documents; nothing is written,
reconciled or recovered here.

A definition that was never revised is judged by the REV-1 baseline rule unchanged. A
revised one is judged against its chain: the historical creation evidence (``store``
record, request digest) must equal the stored baseline revision's copy, and the
projection must equal the applied revision. A valid applied revision is therefore never
reported as a creation conflict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from src.domains.agentverse.agents.definition_revisions.baseline import verify_baseline
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CanonicalizationError,
    canonical_definition,
    definition_digest,
)
from src.domains.agentverse.agents.definition_revisions.models import AgentDefinitionRevision

# Runtime instances (``ManagedAgentV2``) hold no definition; every turn binds the current
# projection at admission (REV-5), so there is no per-instance state to reconcile.
RUNTIME_STATE = "per_turn"


def _records(documents: Sequence[Mapping[str, Any]]) -> list[AgentDefinitionRevision] | None:
    records = []
    for document in documents:
        data = {k: v for k, v in document.items() if k != "_id"}
        try:
            records.append(
                AgentDefinitionRevision.model_validate_json(json.dumps(data, default=str))
            )
        except ValueError:
            return None
    return sorted(records, key=lambda r: r.revision)


def inspect_definition(
    tenant_id: str,
    agent_id: str,
    holders: Sequence[Mapping[str, Any]],
    revision_documents: Sequence[Mapping[str, Any]],
    application_documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """A safe summary (statuses, codes, revision numbers, digests; never content)."""
    applications = [a for a in application_documents if a.get("agent_id") == agent_id]
    incomplete = sorted(
        str(a.get("application_id"))
        for a in applications
        if a.get("state") in ("claimed", "selected", "applied")
    )
    missing_evidence = sorted(
        str(a.get("application_id"))
        for a in applications
        if a.get("state") in ("selected", "applied")
        and not (a.get("selection_event_id") and a.get("application_event_id"))
    )
    document = holders[0] if len(holders) == 1 else None
    marker = (document or {}).get("definition_revision")
    marker = marker if isinstance(marker, Mapping) else None

    if not revision_documents and marker is None:
        summary = verify_baseline(tenant_id, agent_id, holders).summary()
        baseline_codes = list(summary["codes"])
        baseline_status = summary["status"]
        if incomplete:
            baseline_codes.append("application.incomplete")
            if baseline_status == "verified":
                baseline_status = "unverified"
        return {
            **summary,
            "status": baseline_status,
            "codes": baseline_codes,
            "history": "baseline_only",
            "head_revision": None,
            "applied_revision": None,
            "incomplete_applications": incomplete,
            "missing_evidence": missing_evidence,
            "runtime": RUNTIME_STATE,
            "revision_ready": baseline_status == "verified",
        }

    codes: list[str] = []
    records = _records(revision_documents)
    current = None
    if len(holders) != 1:
        codes.append("identity.duplicate")
    elif document is not None:
        try:
            current = definition_digest(tenant_id, agent_id, canonical_definition(document))
        except CanonicalizationError:
            codes.append("definition.invalid_under_current_model")
    head = applied = None
    if records is None:
        codes.append("chain.invalid_record")
    elif not records:
        codes.append("chain.missing")  # A projection marker without any revision record.
    else:
        head = records[-1].revision
        if [r.revision for r in records] != list(range(1, len(records) + 1)) or any(
            later.predecessor is None
            or later.predecessor.revision_digest != earlier.revision_digest
            for earlier, later in pairwise(records)
        ):
            codes.append("chain.broken")
        baseline = records[0]
        store = (document or {}).get("store") or {}
        if baseline.baseline is None or (
            baseline.baseline.creation.request_digest != store.get("request_digest")
            or baseline.baseline.creation.idempotency_scope != store.get("idempotency_scope")
        ):
            codes.append("creation_evidence.changed")
        by_digest = {r.revision_digest: r for r in records}
        applied_record: AgentDefinitionRevision | None
        if marker is None:
            applied_record = records[0]  # Materialized baseline, nothing applied yet.
        else:
            applied_record = by_digest.get(str(marker.get("revision_digest")))
            if applied_record is None or applied_record.revision != marker.get("revision"):
                codes.append("projection.unknown_revision")
        if applied_record is not None:
            applied = applied_record.revision if marker is not None else None
            if current is not None and current != applied_record.definition_digest:
                codes.append("projection.direct_mutation")
            if head is not None and head > applied_record.revision:
                codes.append("revision.selected_unapplied")
    if incomplete:
        codes.append("application.incomplete")
    if missing_evidence:
        codes.append("application.missing_evidence")

    conflicting = {
        "identity.duplicate",
        "definition.invalid_under_current_model",
        "chain.invalid_record",
        "chain.missing",
        "chain.broken",
        "creation_evidence.changed",
        "projection.unknown_revision",
        "projection.direct_mutation",
    }
    if conflicting & set(codes):
        status = "conflicted"
    elif codes:
        status = "unverified"  # In-flight or incomplete, not contradictory.
    else:
        status = "verified"
    if "revision.selected_unapplied" in codes:
        history = "selected_unapplied"
    elif applied is not None:
        history = "applied"
    else:
        history = "baseline_materialized"
    store = (document or {}).get("store") or {}
    return {
        "agent_id": agent_id,
        "status": status,
        "codes": sorted(set(codes)),
        "creation_evidence": "available" if store else "missing",
        "rule": "chain",
        "duplicate_count": len(holders),
        "current_definition_digest": current,
        "recorded_request_digest": store.get("request_digest"),
        "recomputed_request_digest": None,
        "modified_after_creation": None,
        "history": history,
        "head_revision": head,
        "applied_revision": applied,
        "incomplete_applications": incomplete,
        "missing_evidence": missing_evidence,
        "runtime": RUNTIME_STATE,
        "revision_ready": status == "verified",
    }
