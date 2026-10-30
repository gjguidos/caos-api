"""Versioned CAOS audit log contract (``coll_logs``).

A record is immutable evidence of something a governed operation did. Producers
describe *what* happened (``LogEvent``); trusted code supplies *who* and *under which
authority* (``AuthorizationProvenance`` from a verified UTU permit) and the service
clock supplies *when*. Neither identity, tenant, time nor authorization can be set
through an event or its metadata. Records grant nothing and hold no permit, session,
token or payload body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from src.base.operations.v2.admission import (
    AuthorizationProvenance,
    Digest,
    Identifier,
    Signal,
    canonical_json,
    sha256_hex,
)
from src.base.operations.v2.models import Timestamp

LOG_SCHEMA_VERSION = 1
LOGS_COLLECTION = "coll_logs"
# Audit records stay small and independently insertable. Every field is bounded (the
# largest field-valid record is ~8.3 KiB); this whole-record bound also caps fields
# whose reused types are unbounded (e.g. ``Signal``). Large or high-cardinality
# evidence belongs in separate, versioned artifacts referenced by digest (deferred).
MAX_RECORD_BYTES = 16 * 1024

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}")
_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SLOT = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _matches(pattern: re.Pattern[str], label: str):
    def check(value: str) -> str:
        if not pattern.fullmatch(value):
            raise ValueError(f"Invalid {label}")
        return value

    return check


def _millisecond_utc(value: datetime) -> datetime:
    # MongoDB/Cosmos store millisecond precision; truncate so round trips are exact.
    value = value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


IssueCode = Annotated[StrictStr, AfterValidator(_matches(_CODE, "code"))]
EventId = Annotated[StrictStr, AfterValidator(_matches(_EVENT_ID, "event ID"))]
Slot = Annotated[StrictStr, AfterValidator(_matches(_SLOT, "slot"))]
# Fixed-width UTC text: deterministic digests and lexicographic order equal time order.
LogTimestamp = Annotated[
    Timestamp,
    AfterValidator(_millisecond_utc),
    PlainSerializer(lambda v: v.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", return_type=str),
]
IssueCodes = Annotated[tuple[IssueCode, ...], Field(max_length=64)]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


# ---------------------------------------------------------------------------
# Event-specific metadata allowlists. Unknown keys are rejected, values typed and
# bounded; free text, payloads and credentials are not expressible.
# ---------------------------------------------------------------------------


class GenesisInitiationMetadata(_Frozen):
    event_version: Literal[1]
    slot: Literal["enki", "thoth"]
    executor_id: Identifier
    creation_digest: Digest


class GenesisValidationMetadata(_Frozen):
    resource_type: Literal["agent"]
    valid: StrictBool
    issue_codes: IssueCodes = ()
    normalized_digest: Digest | None = None


class GenesisCompositionMetadata(_Frozen):
    composed: StrictBool
    issue_codes: IssueCodes = ()
    validation_digest: Digest | None = None


class GenesisStoreCompletedMetadata(_Frozen):
    resource_type: Literal["agent"]
    created: StrictBool
    request_digest: Digest
    slot: Slot | None = None


class GenesisStoreFailedMetadata(_Frozen):
    resource_type: Literal["agent"]
    code: IssueCode
    retryable: StrictBool


class GenesisActivationMetadata(_Frozen):
    """Khepri's separately authorized activation of a Genesis-provisioned agent."""

    slot: Slot
    state: Literal["active", "denied", "failed"]
    already_active: StrictBool = False
    code: IssueCode | None = None


class GenesisReadinessMetadata(_Frozen):
    """Observed checks only; a check that did not run is never reported as passed."""

    slot: Slot
    probe: Literal["chat.context"]
    context_ok: StrictBool
    grants_ok: StrictBool
    backend_ok: StrictBool
    code: IssueCode | None = None


class AgentCreationReviewMetadata(_Frozen):
    """A human decision on an exact reviewed draft; never creation or activation authority."""

    event_version: Literal[1]
    approval_id: Identifier
    review_digest: Digest
    normalized_digest: Digest
    source_agent_id: Identifier


class AgentCreationMetadata(_Frozen):
    """Governed manual creation of one approved Agent (G3.4). Identifiers and digests
    only; the reviewed snapshot and credentials are never audit metadata."""

    event_version: Literal[1]
    creation_id: Identifier
    approval_id: Identifier
    review_digest: Digest
    source_agent_id: Identifier
    target_agent_id: Identifier
    store_request_digest: Digest | None = None
    created: StrictBool | None = None


class AgentDraftProposalDecisionMetadata(_Frozen):
    """A human decision on one draft proposal (DRAFT-0.2A). Identifiers, revisions,
    field names and fingerprints only; never draft content, rationale or prompts."""

    event_version: Literal[1]
    proposal_id: Identifier
    draft_id: Identifier
    source_agent_id: Identifier
    proposer_agent_id: Identifier
    base_revision: Annotated[int, Field(strict=True, ge=1)]
    resulting_revision: Annotated[int, Field(strict=True, ge=1)] | None = None
    accepted_fields: Annotated[list[Slot], Field(max_length=16)]
    rejected_fields: Annotated[list[Slot], Field(max_length=16)]
    proposal_digest: Digest
    decision_digest: Digest
    content_digest: Digest | None = None


Outcome = Literal["succeeded", "failed", "rejected"]


@dataclass(frozen=True)
class _EventSpec:
    metadata: type[BaseModel]
    outcomes: frozenset[str]


EVENT_CATALOG: dict[str, _EventSpec] = {
    "genesis.initiation.authorized": _EventSpec(
        metadata=GenesisInitiationMetadata, outcomes=frozenset({"succeeded"})
    ),
    "genesis.validation.completed": _EventSpec(
        metadata=GenesisValidationMetadata, outcomes=frozenset({"succeeded", "rejected"})
    ),
    "genesis.composition.completed": _EventSpec(
        metadata=GenesisCompositionMetadata, outcomes=frozenset({"succeeded", "rejected"})
    ),
    "genesis.store.completed": _EventSpec(
        metadata=GenesisStoreCompletedMetadata, outcomes=frozenset({"succeeded"})
    ),
    "genesis.store.failed": _EventSpec(
        metadata=GenesisStoreFailedMetadata, outcomes=frozenset({"failed", "rejected"})
    ),
    "genesis.activation.completed": _EventSpec(
        metadata=GenesisActivationMetadata,
        outcomes=frozenset({"succeeded", "failed", "rejected"}),
    ),
    "genesis.readiness.completed": _EventSpec(
        metadata=GenesisReadinessMetadata, outcomes=frozenset({"succeeded", "failed"})
    ),
    "agent.creation_review.approved": _EventSpec(
        metadata=AgentCreationReviewMetadata, outcomes=frozenset({"succeeded"})
    ),
    "agent.creation_review.revoked": _EventSpec(
        metadata=AgentCreationReviewMetadata, outcomes=frozenset({"succeeded"})
    ),
    "agent.creation.authorized": _EventSpec(
        metadata=AgentCreationMetadata, outcomes=frozenset({"succeeded"})
    ),
    "agent.creation.completed": _EventSpec(
        metadata=AgentCreationMetadata, outcomes=frozenset({"succeeded"})
    ),
    "agent.draft.proposal.accepted": _EventSpec(
        metadata=AgentDraftProposalDecisionMetadata, outcomes=frozenset({"succeeded"})
    ),
    "agent.draft.proposal.rejected": _EventSpec(
        metadata=AgentDraftProposalDecisionMetadata, outcomes=frozenset({"succeeded"})
    ),
}
EventType = Literal[
    "genesis.initiation.authorized",
    "genesis.validation.completed",
    "genesis.composition.completed",
    "genesis.store.completed",
    "genesis.store.failed",
    "genesis.activation.completed",
    "genesis.readiness.completed",
    "agent.creation_review.approved",
    "agent.creation_review.revoked",
    "agent.creation.authorized",
    "agent.creation.completed",
    "agent.draft.proposal.accepted",
    "agent.draft.proposal.rejected",
]


# ---------------------------------------------------------------------------
# Structured parts
# ---------------------------------------------------------------------------


class LogSubject(_Frozen):
    """The resource the event is about (e.g. the Enki definition)."""

    resource_type: Literal["agent"]
    resource_id: Identifier | None = None


class LogTarget(_Frozen):
    """The exact capability invoked."""

    signal: Signal
    version: Identifier


class LogCorrelation(_Frozen):
    """Existing identifiers only: CPBX/Event V2 correlation and causation, OPS V2 operation."""

    correlation_id: Identifier
    causation_id: Identifier | None = None
    operation_id: UUID | None = None


class LogActor(_Frozen):
    """Derived from authorization provenance: the executing principal and the
    authorizing user it acted for. Never supplied by a producer."""

    principal_id: Identifier
    user_id: Identifier


# ---------------------------------------------------------------------------
# Producer input and persisted record
# ---------------------------------------------------------------------------


class LogEvent(_Frozen):
    """What a trusted producer may describe. Identity, tenant, time and authorization
    are deliberately absent and rejected as unknown fields."""

    event_id: EventId
    event_type: EventType
    outcome: Outcome
    subject: LogSubject
    target: LogTarget
    correlation: LogCorrelation
    metadata: dict[str, Any]

    @field_validator("metadata")
    @classmethod
    def _allowlisted(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        event_type = info.data.get("event_type")
        if event_type not in EVENT_CATALOG:
            raise ValueError("Metadata requires a known event type")
        # Validate and canonicalize through the event's allowlist model.
        return EVENT_CATALOG[event_type].metadata.model_validate(value).model_dump(mode="json")

    @model_validator(mode="after")
    def _outcome(self) -> Self:
        if self.outcome not in EVENT_CATALOG[self.event_type].outcomes:
            raise ValueError(f"Outcome {self.outcome!r} is not valid for {self.event_type}")
        return self


def stable_event_id(*, correlation_id: str, event_type: str, step: str) -> str:
    """Deterministic event ID for one step of one correlated operation, so a retry
    of the same step is ingested once."""
    digest = sha256_hex({"correlation_id": correlation_id, "event_type": event_type, "step": step})
    return str(UUID(digest[:32]))


class CAOSLogRecord(_Frozen):
    """Persisted, immutable audit record. ``content_digest`` covers everything except
    ``recorded_at``, so an identical retry is recognized as the same event."""

    schema_version: Literal[1]
    event_id: EventId
    event_type: EventType
    outcome: Outcome
    tenant_id: Identifier
    actor: LogActor
    authorization: AuthorizationProvenance
    subject: LogSubject
    target: LogTarget
    correlation_id: Identifier
    causation_id: Identifier | None
    operation_id: UUID | None
    metadata: dict[str, Any]
    recorded_at: LogTimestamp
    content_digest: Digest

    _DIGEST_EXCLUDES: ClassVar[frozenset[str]] = frozenset({"recorded_at", "content_digest"})

    @classmethod
    def build(
        cls, event: LogEvent, *, authorization: AuthorizationProvenance, recorded_at: datetime
    ) -> CAOSLogRecord:
        body = {
            "schema_version": LOG_SCHEMA_VERSION,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "outcome": event.outcome,
            "tenant_id": authorization.tenant_id,
            "actor": {
                "principal_id": authorization.principal_id,
                "user_id": authorization.subject_id,
            },
            "authorization": authorization.model_dump(mode="json"),
            "subject": event.subject.model_dump(mode="json"),
            "target": event.target.model_dump(mode="json"),
            "correlation_id": event.correlation.correlation_id,
            "causation_id": event.correlation.causation_id,
            "operation_id": (
                str(event.correlation.operation_id) if event.correlation.operation_id else None
            ),
            "metadata": event.metadata,
        }
        return cls.model_validate(
            {**body, "recorded_at": recorded_at, "content_digest": sha256_hex(body)}
        )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.tenant_id != self.authorization.tenant_id or (
            self.actor.principal_id,
            self.actor.user_id,
        ) != (self.authorization.principal_id, self.authorization.subject_id):
            raise ValueError("Record identity must match its authorization provenance")
        spec = EVENT_CATALOG[self.event_type]
        if self.outcome not in spec.outcomes:
            raise ValueError("Outcome is not valid for the event type")
        if spec.metadata.model_validate(self.metadata).model_dump(mode="json") != self.metadata:
            raise ValueError("Metadata is not canonical for the event type")
        if self.content_digest != sha256_hex(self.digest_body()):
            raise ValueError("Content digest mismatch")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > MAX_RECORD_BYTES:
            raise ValueError("Audit record exceeds the size bound; reference an artifact")
        return self

    def digest_body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude=set(self._DIGEST_EXCLUDES))

    def to_document(self) -> dict[str, Any]:
        """Deterministic storage form. ``_id`` is tenant-scoped, so equal event IDs in
        different tenants never collide or reveal one another."""
        return {"_id": storage_id(self.tenant_id, self.event_id), **self.model_dump(mode="json")}


def storage_id(tenant_id: str, event_id: str) -> str:
    return sha256_hex({"tenant_id": tenant_id, "event_id": event_id})
