"""Read-only baseline verification of an existing Agent definition (REV-1).

Compares the current persisted definition with its historical creation evidence, the
``store.create`` record, using that record's own canonicalization rule. Historical
digests are never rewritten or reinterpreted; differences are not normalized away.

Outcomes (never silently upgraded):

``verified``
    Exactly one document holds the identity; the creation record is well formed, names the
    document's tenant and creator, and recomputing its rule over the current document
    reproduces its ``request_digest``; no unattributable field is present.
``unverified``
    Evidence exists but cannot establish equality: the digest differs with no evidence of a
    later write (consistent with ``Agent`` model evolution since creation), the document has
    fields the rule cannot account for, or it is invalid under the current model.
``conflicted``
    Evidence contradicts the baseline: duplicate documents for the identity, creation
    provenance naming another tenant or creator, an unexpected revision marker, or a
    differing digest together with evidence of a post-creation write.
``unavailable``
    No definition in the tenant, no creation evidence (e.g. V1 or seeded definitions), a
    malformed or unsupported creation record, or the store could not be read.

Equivalence rule (documented, the only one applied): the ``store.create`` v1 digest is
``sha256({"resource_type": "agent", "definition": Agent(doc).model_dump(mode="json")})``
over the document without storage metadata. Nothing else is treated as equivalent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CanonicalizationError,
    agent_model_dump,
    canonical_definition,
    definition_digest,
    unknown_fields,
)
from src.domains.agentverse.agents.definition_revisions.models import (
    STORE_RULE_V1,
    AgentDefinitionRevision,
    BaselineProvenance,
    BaselineVerificationRecord,
    CreationEvidence,
    RevisionCorrelation,
)

BaselineStatus = Literal["verified", "unverified", "conflicted", "unavailable"]
EvidenceState = Literal["available", "missing", "malformed", "unsupported"]


@dataclass(frozen=True)
class BaselineVerification:
    """Safe facts only: identities, statuses, codes and digests. Never definition text."""

    tenant_id: str
    agent_id: str
    status: BaselineStatus
    codes: tuple[str, ...] = ()
    creation_evidence: EvidenceState = "missing"
    rule: str | None = None
    duplicate_count: int = 0
    current_definition_digest: str | None = None
    recorded_request_digest: str | None = None
    recomputed_request_digest: str | None = None
    modified_after_creation: bool | None = None
    evidence: CreationEvidence | None = field(default=None, repr=False)

    @property
    def revision_ready(self) -> bool:
        """Whether a future governed revision could start from this baseline."""
        return self.status == "verified"

    def summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "codes": list(self.codes),
            "creation_evidence": self.creation_evidence,
            "rule": self.rule,
            "duplicate_count": self.duplicate_count,
            "current_definition_digest": self.current_definition_digest,
            "recorded_request_digest": self.recorded_request_digest,
            "recomputed_request_digest": self.recomputed_request_digest,
            "modified_after_creation": self.modified_after_creation,
            "revision_ready": self.revision_ready,
        }


def store_rule_v1_digest(document: Mapping[str, Any]) -> str:
    """The historical ``store.create`` v1 request digest, recomputed (raises
    ``CanonicalizationError`` when the document is invalid under the current model)."""
    return sha256_hex({"resource_type": "agent", "definition": agent_model_dump(document)})


def verify_baseline(
    tenant_id: str, agent_id: str, documents: Sequence[Mapping[str, Any]]
) -> BaselineVerification:
    """Pure verification over every persisted document holding ``agent_id`` (all
    tenants, so duplicate identities are visible)."""

    def result(status: BaselineStatus, *codes: str, **facts: Any) -> BaselineVerification:
        return BaselineVerification(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
            codes=tuple(codes),
            duplicate_count=len(documents),
            **facts,
        )

    if len(documents) > 1:
        return result("conflicted", "identity.duplicate")
    if not documents or documents[0].get("tenant_id") != tenant_id:
        return result("unavailable", "definition.missing")
    document = documents[0]
    try:
        current = definition_digest(tenant_id, agent_id, canonical_definition(document))
    except CanonicalizationError:
        current = None
    if document.get("definition_revision") is not None:
        # No writer exists before REV-4: a projection marker is unexplained.
        return result(
            "conflicted", "projection.unexpected_marker", current_definition_digest=current
        )
    store = document.get("store")
    if store is None:
        code = (
            "creation_evidence.seed"
            if document.get("genesis_local_seed") is True
            else ("creation_evidence.missing")
        )
        return result("unavailable", code, current_definition_digest=current)
    if not isinstance(store, Mapping) or store.get("version") != 1:
        state: EvidenceState = "unsupported" if isinstance(store, Mapping) else "malformed"
        return result(
            "unavailable",
            f"creation_evidence.{state}",
            creation_evidence=state,
            current_definition_digest=current,
        )
    try:
        evidence = CreationEvidence.model_validate(
            {
                "store_version": store.get("version"),
                "operation": store.get("operation"),
                "resource_type": store.get("resource_type"),
                "idempotency_scope": store.get("idempotency_scope"),
                "request_digest": store.get("request_digest"),
                "stored_at": _utc(store.get("stored_at")),
                "authorization": store.get("authorization"),
            },
            strict=False,
        )
    except (ValidationError, TypeError, ValueError):
        return result(
            "unavailable",
            "creation_evidence.malformed",
            creation_evidence="malformed",
            current_definition_digest=current,
        )
    facts: dict[str, Any] = {
        "creation_evidence": "available",
        "rule": STORE_RULE_V1,
        "current_definition_digest": current,
        "recorded_request_digest": evidence.request_digest,
        "evidence": evidence,
    }
    authority = evidence.authorization
    if (authority.tenant_id, authority.subject_id) != (
        document.get("tenant_id"),
        document.get("creator"),
    ) or document.get("id") != agent_id:
        return result("conflicted", "creation_evidence.identity_mismatch", **facts)
    modified = _modified_after(document, evidence.stored_at)
    facts["modified_after_creation"] = modified
    try:
        recomputed = store_rule_v1_digest(document)
    except CanonicalizationError:
        return result("unverified", "definition.invalid_under_current_model", **facts)
    facts["recomputed_request_digest"] = recomputed
    extras = unknown_fields(document)
    if recomputed != evidence.request_digest:
        if modified:
            return result("conflicted", "definition.modified_after_creation", **facts)
        return result("unverified", "definition.canonicalization_mismatch", **facts)
    if extras:
        return result("unverified", "definition.unattributed_fields", **facts)
    return result(
        "verified", *(("definition.touched_after_creation",) if modified else ()), **facts
    )


def baseline_revision(
    verification: BaselineVerification,
    document: Mapping[str, Any],
    *,
    recorded_at: datetime,
    correlation: RevisionCorrelation | None = None,
) -> AgentDefinitionRevision:
    """Revision 1 for a **verified** baseline only (pure; it writes nothing)."""
    if (
        verification.status != "verified"
        or verification.evidence is None
        or verification.recomputed_request_digest is None
    ):
        raise ValueError("Only a verified baseline can become revision 1")
    return AgentDefinitionRevision.build(
        tenant_id=verification.tenant_id,
        agent_id=verification.agent_id,
        revision=1,
        origin="baseline",
        definition=canonical_definition(document),
        baseline=BaselineProvenance(
            creation=verification.evidence,
            verification=BaselineVerificationRecord(
                status="verified",
                rule=STORE_RULE_V1,
                recomputed_request_digest=verification.recomputed_request_digest,
            ),
        ),
        correlation=correlation or RevisionCorrelation(),
        recorded_at=recorded_at,
    )


class DefinitionReader(Protocol):
    async def find(
        self, collection: str, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


async def inspect_baseline(
    reader: DefinitionReader, tenant_id: str, agent_id: str, *, collection: str = "coll_agents"
) -> BaselineVerification:
    """Read-only: one ``find`` by Agent ID across tenants; any failure is ``unavailable``."""
    try:
        documents = await reader.find(collection, {"id": agent_id}, {"_id": 0})
    except Exception:  # noqa: BLE001 - an unreadable store is reported, never assumed
        return BaselineVerification(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status="unavailable",
            codes=("store.unavailable",),
        )
    return verify_baseline(tenant_id, agent_id, documents)


def _utc(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _modified_after(document: Mapping[str, Any], stored_at: datetime) -> bool | None:
    """Evidence of a post-creation write: Store sets every timestamp to ``stored_at``;
    ``DBService.update_agent`` advances ``updated_at``. ``None``: no usable timestamp."""
    stamps = [
        _utc(document.get(name))
        for name in ("updated_at", "modified")
        if isinstance(document.get(name), datetime)
    ]
    if not stamps:
        return None
    return any(stamp != stored_at for stamp in stamps)
