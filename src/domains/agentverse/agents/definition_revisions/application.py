"""Governed application of an approved Agent revision (REV-4, ADR-0005).

States, kept distinct (ADR-0005 §12):

1. **Approval recorded** (REV-3).
2. **Application claimed**: one application record per ``(tenant, Agent, predecessor)``
   (``_id = claim_id``) is inserted, then the approval is consumed by that claim. A
   withdrawal committed before consumption wins; after consumption withdrawal is refused
   (the point of no withdrawal) and approval expiry no longer applies.
3. **Successor selected**: the REV-1 insert-only record ``N+1`` (an identical retry
   resolves the same record; a competing candidate is a deterministic conflict).
4. **Definition applied**: one compare-and-set of the ``coll_agents`` projection against
   the exact predecessor, setting ``definition_revision``.
5. **Runtime reconciliation pending** (REV-5, not implemented).

Execution reuses OPS V2 managed operations (``operation_id = application_id``): leased,
fenced attempts; an expired attempt is reconciled to retry-pending, **never** to failure,
because a lease expiry is not evidence that a partial Store application failed. Every
Store step is idempotent and re-derived from durable state, so a retry completes exactly
the selected revision.

The only writer is the ``store.revise.approved@1.0.0`` capability (provider
``system.store``), executable only through the exact ``agent.definition.revise`` scope by
the Genesis executor named in the approval. Not atomic across documents or stores: the
claim, revision record, projection, approval and Seshat evidence are separate writes,
ordered so that every intermediate state is non-effective or completed by a retry.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.base.operations.v2.managed import (
    AttemptConflict,
    EpochStatus,
    ManagedOperation,
    ManagedOperationRepository,
)
from src.base.operations.v2.models import AttemptState, OperationRecord, OperationState
from src.base.operations.v2.ownership import (
    LeaseExpired,
    ObsoleteFencingToken,
    OwnershipConflict,
    OwnershipReleased,
)
from src.base.operations.v2.repository import DuplicateOperation
from src.base.operations.v2.repository import RevisionConflict as OperationRevisionConflict
from src.base.operations.v2.transitions import finish_attempt
from src.domains.agentverse.agents.definition_revisions.approval import (
    RevisionApproval,
    RevisionApprovalConsumed,
    RevisionApprovalError,
    RevisionApprovalExpired,
    RevisionApprovalService,
    RevisionApprovalWithdrawn,
)
from src.domains.agentverse.agents.definition_revisions.baseline import (
    baseline_revision,
    verify_baseline,
)
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CanonicalizationError,
    canonical_definition,
    definition_digest,
)
from src.domains.agentverse.agents.definition_revisions.drafts import PredecessorRef
from src.domains.agentverse.agents.definition_revisions.models import (
    AgentDefinitionRevision,
    Predecessor,
    RevisionConflict,
    RevisionCorrelation,
    RevisionError,
)
from src.domains.agentverse.agents.definition_revisions.repository import (
    MongoAgentDefinitionRevisionRepository,
)
from src.domains.agentverse.agents.definition_revisions.review import (
    MongoRevisionReviewRepository,
)
from src.domains.agentverse.agents.definition_revisions.service import COMPOSED_FIELDS
from src.domains.agentverse.audit.models import (
    EventType,
    LogCorrelation,
    LogEvent,
    LogSubject,
    LogTarget,
    stable_event_id,
)
from src.domains.agentverse.audit.repository import LogConflict, LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.runtime.compatibility import is_governed_definition
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.tools.base import ToolResult
from src.domains.agentverse.tools.store_tool.spec import STORE_PROVIDER, STORE_TOOL_SPEC

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository
    from src.domains.agentverse.agents.definition_revisions.service import (
        AgentDefinitionRevisionService,
        Policy,
    )
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
    from src.domains.agentverse.ncsc.models import Synapse

REVISE_SIGNAL: Final = "store.revise.approved"
REVISE_VERSION: Final = "1.0.0"
REVISE_PROVIDER: Final = STORE_PROVIDER
REVISE_OPERATION: Final = "agent.definition.revise"
REVISE_BINDING: Final = CapabilityBinding(REVISE_SIGNAL, REVISE_PROVIDER, REVISE_VERSION)
REVISE_SCOPE: Final = CapabilityScope(REVISE_OPERATION, REVISE_BINDING, frozenset({REVISE_BINDING}))
APPLICATIONS_COLLECTION: Final = "coll_agent_definition_revision_applications"
APPLICATION_CONTRACT: Final = "caos.agent_definition_revision_application"
OPERATION_KIND: Final = "agent.definition.revise"
OPERATION_PRODUCER: Final = "genesis.revision"
SELECTED: Final = "agent.definition_revision.selected"
APPLIED: Final = "agent.definition_revision.applied"
ApplicationState = Literal["claimed", "selected", "applied", "completed", "failed"]
_AGENTS: Final = "coll_agents"


class RevisionApplicationError(Exception):
    code = "revision_application.error"
    retryable = False


class RevisionApplicationConflict(RevisionApplicationError):
    def __init__(self, reason: str, *, selected_revision_digest: str | None = None) -> None:
        super().__init__("The revision cannot be applied")
        self.code = f"revision_application.{reason}"
        # REV-5: set when a successor is already selected, so the claim is never
        # recorded as unselected (and never re-claimable) while a selection exists.
        self.selected_revision_digest = selected_revision_digest


class RevisionApplicationInProgress(RevisionApplicationError):
    code = "revision_application.in_progress"
    retryable = True


class RevisionApplicationUnavailable(RevisionApplicationError):
    code = "revision_application.unavailable"
    retryable = True


class RevisionApplicationNotFound(RevisionApplicationError):
    code = "revision_application.not_found"


class RevisionApplicationCorrupt(RevisionApplicationError):
    code = "revision_application.corrupt"


def claim_id(tenant_id: str, agent_id: str, predecessor_revision_digest: str) -> str:
    """One claim per predecessor: a failed or expired worker cannot let another candidate
    apply over the same predecessor while its claim stands."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-definition-revision-claim:"
            + sha256_hex(
                {"tenant": tenant_id, "agent": agent_id, "predecessor": predecessor_revision_digest}
            ),
        )
    )


def application_id(claim: str, approval: RevisionApproval) -> str:
    """Binds the Agent and predecessor (claim), the exact candidate, review and human."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-definition-revision-application:"
            + sha256_hex(
                {
                    "claim": claim,
                    "approval": approval.approval_id,
                    "subject": approval.subject_id,
                    "review_digest": approval.review_digest,
                    "candidate": approval.candidate_definition_digest,
                }
            ),
        )
    )


class RevisionApplication(BaseModel):
    """The trusted claim and application record (``_id = claim_id``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_definition_revision_application"] = APPLICATION_CONTRACT
    schema_version: Literal[1] = 1
    claim_id: str = Field(min_length=1, max_length=64)
    application_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    executor_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=64)
    approval_event_id: str = Field(min_length=1, max_length=128)
    review_id: str = Field(min_length=1, max_length=64)
    review_digest: Digest
    candidate_definition_digest: Digest
    predecessor: PredecessorRef
    target_revision: int = Field(ge=2)
    state: ApplicationState
    # The scoped Genesis authority at claim time, reused so evidence is identical.
    authorization: AuthorizationProvenance
    claimed_at: datetime
    updated_at: datetime
    selected_revision_digest: Digest | None = None
    applied_at: datetime | None = None
    selection_event_id: str | None = None
    application_event_id: str | None = None
    completed_at: datetime | None = None
    issue_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.claim_id != claim_id(
            self.tenant_id, self.agent_id, self.predecessor.revision_digest
        ):
            raise ValueError("Claim identity mismatch")
        if self.target_revision != self.predecessor.revision + 1:
            raise ValueError("The target is exactly the predecessor's successor")
        if self.state in ("selected", "applied", "completed") and not self.selected_revision_digest:
            raise ValueError("A selected application names its revision")
        if (self.state in ("applied", "completed")) != (self.applied_at is not None) and (
            self.state != "failed"
        ):
            raise ValueError("Applied time is recorded exactly when applied")
        if self.state == "completed" and not (
            self.selection_event_id and self.application_event_id and self.completed_at
        ):
            raise ValueError("Completion requires its evidence")
        if (self.state == "failed") != bool(self.issue_codes):
            raise ValueError("Only a failed application has issue codes")
        if self.authorization.principal_id != self.executor_id or (
            self.authorization.tenant_id,
            self.authorization.subject_id,
        ) != (self.tenant_id, self.subject_id):
            raise ValueError("Application authority differs from its claim")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "schema_version": self.schema_version,
            "application_id": self.application_id,
            "agent_id": self.agent_id,
            "approval_id": self.approval_id,
            "state": self.state,
            "target_revision": self.target_revision,
            "revision_digest": self.selected_revision_digest,
            "issue_codes": list(self.issue_codes),
            "retryable": self.state in ("claimed", "selected", "applied"),
            # REV-5: runtimes are not reconciled here; nothing is activated.
            "runtime": "reconciliation_pending" if self.state == "completed" else None,
            "authorizes": {"activation": False, "grants": False},
        }


def _dump(record: BaseModel) -> dict[str, Any]:
    return json.loads(record.model_dump_json())


class MongoRevisionApplicationRepository:
    """``_id = claim_id``: insert decides the claim; state changes by compare-and-set."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection: str = APPLICATIONS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection)
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable("Application store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise RevisionApplicationUnavailable("Applications require acknowledged writes")
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    async def get(self, tenant_id: str, claim: str) -> RevisionApplication | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": claim, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable("Application store is unavailable") from exc
        if document is None:
            return None
        try:
            data = dict(document)
            data.pop("_id")
            return RevisionApplication.model_validate_json(json.dumps(data))
        except (ValueError, TypeError) as exc:
            raise RevisionApplicationCorrupt("Invalid stored application") from exc

    async def claim(self, record: RevisionApplication) -> RevisionApplication:
        """Insert the claim, or re-claim a failed claim that never selected a revision.
        Returns the stored claim either way (the caller compares identities)."""
        collection = await self._collection()
        try:
            await collection.insert_one({"_id": record.claim_id, **_dump(record)})
            return record
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable("Claim outcome is unknown") from exc
        try:
            await collection.replace_one(
                {
                    "_id": record.claim_id,
                    "tenant_id": record.tenant_id,
                    "state": "failed",
                    "selected_revision_digest": None,
                    "application_id": {"$ne": record.application_id},
                },
                {"_id": record.claim_id, **_dump(record)},
            )
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable("Claim outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.claim_id)
        if stored is None:
            raise RevisionApplicationUnavailable("Claim outcome is unknown")
        return stored

    async def transition(
        self,
        record: RevisionApplication,
        *,
        expected: tuple[ApplicationState, ...],
        changes: dict[str, Any],
    ) -> RevisionApplication:
        updated = RevisionApplication.model_validate({**record.model_dump(), **changes})
        values = {key: _dump(updated)[key] for key in changes}
        collection = await self._collection()
        try:
            await collection.update_one(
                {
                    "_id": record.claim_id,
                    "tenant_id": record.tenant_id,
                    "application_id": record.application_id,
                    "state": {"$in": list(expected)},
                },
                {"$set": values},
            )
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable(
                "Application transition outcome is unknown"
            ) from exc
        stored = await self.get(record.tenant_id, record.claim_id)
        if stored is None:
            raise RevisionApplicationCorrupt("Application disappeared during a transition")
        return stored

    async def missing_evidence(
        self, tenant_id: str, *, limit: int = 100
    ) -> list[RevisionApplication]:
        """Operator discovery: applications whose Store effect may lack Seshat evidence
        (selected or applied, not completed). Bounded scan; no index required."""
        collection = await self._collection()
        try:
            documents = await collection.find(
                {"tenant_id": tenant_id, "state": {"$in": ["selected", "applied"]}}
            ).to_list(length=limit)
        except PyMongoError as exc:
            raise RevisionApplicationUnavailable("Application store is unavailable") from exc
        found = []
        for document in documents:
            data = dict(document)
            data.pop("_id")
            found.append(RevisionApplication.model_validate_json(json.dumps(data)))
        return found


# --- The Store capability (the only writer) ------------------------------------------------


class RevisionStoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    resource_type: Literal["agent"]
    claim_id: str = Field(min_length=1, max_length=64)
    application_id: str = Field(min_length=1, max_length=64)


class AgentProjection:
    """Exact-identity reads and the single predecessor compare-and-set on ``coll_agents``."""

    def __init__(self, mongodb_repository: MongoDBRepository, collection: str = _AGENTS) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def holders(self, agent_id: str) -> list[dict[str, Any]]:
        collection = await self.repository.get_collection(self.collection)
        return await collection.find({"id": agent_id}, {"_id": 0}).to_list(length=10)

    async def compare_and_set(
        self, document: Mapping[str, Any], expected: dict[str, Any], values: dict[str, Any]
    ) -> bool:
        collection = await self.repository.get_collection(self.collection)
        result = await collection.update_one(
            {"id": document["id"], "tenant_id": document["tenant_id"], **expected},
            {"$set": values},
        )
        return bool(result.modified_count == 1)


class RevisionStoreCapability:
    """``store.revise.approved@1.0.0``: select and apply exactly one claimed revision.

    Executable only with a session-origin permit attenuated to ``agent.definition.revise``
    (exact binding chain, entry operation and provider version), by the executor recorded
    in the claim, for the claim's human and tenant. It re-verifies the consumed approval
    and its evidence, the immutable review, unique Agent identity and the predecessor at
    the write. Every step is idempotent: a retry resolves the same revision record and
    recognizes an already-applied projection.
    """

    def __init__(
        self,
        *,
        authority: UTUAuthority,
        applications: MongoRevisionApplicationRepository,
        approvals: RevisionApprovalService,
        reviews: MongoRevisionReviewRepository,
        revisions: MongoAgentDefinitionRevisionRepository,
        projection: AgentProjection,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._authority = authority
        self._applications = applications
        self._approvals = approvals
        self._reviews = reviews
        self._revisions = revisions
        self._projection = projection
        self._clock = clock

    async def revise(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        permit = client.execution_permit
        if (
            not isinstance(permit, ExecutionPermit)
            or (permit.key.tenant_id, permit.key.agent_id) != (synapse.tenant_id, synapse.source)
            or permit.scope != REVISE_SCOPE
            or not permit.binding_chain
            or permit.binding_chain[-1] != REVISE_BINDING
            or synapse.entry_operation != REVISE_SCOPE.operation
            or synapse.provider_version != REVISE_VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError("store.revise.approved requires its revision scope")
        await self._authority.check_permit(permit)
        try:
            request = RevisionStoreRequest.model_validate(synapse.payload)
        except ValidationError:
            return _failure("revise.invalid_request", retryable=False)
        try:
            result = await self._apply(permit, request)
        except RevisionApplicationConflict as exc:
            return _failure(
                exc.code,
                retryable=exc.retryable,
                selected_revision_digest=exc.selected_revision_digest,
            )
        except (RevisionApplicationError, RevisionApprovalError, RevisionError) as exc:
            return _failure(exc.code, retryable=exc.retryable)
        return ToolResult(success=True, result=result).model_dump()

    async def _apply(
        self, permit: ExecutionPermit, request: RevisionStoreRequest
    ) -> dict[str, Any]:
        key = permit.key
        record = await self._applications.get(key.tenant_id, request.claim_id)
        if (
            record is None
            or record.application_id != request.application_id
            or (record.subject_id, record.executor_id) != (key.user_id, key.agent_id)
            or record.state == "failed"
        ):
            raise RevisionApplicationConflict("claim_mismatch")
        approval = await self._approvals.verify_claimed(
            record.tenant_id, record.approval_id, record.application_id
        )
        if (
            approval.agent_id,
            approval.review_id,
            approval.review_digest,
            approval.candidate_definition_digest,
            approval.predecessor,
        ) != (
            record.agent_id,
            record.review_id,
            record.review_digest,
            record.candidate_definition_digest,
            record.predecessor,
        ):
            raise RevisionApplicationConflict("approval_mismatch")
        review = await self._reviews.get(record.tenant_id, record.review_id)
        if review is None or review.review_digest != record.review_digest:
            raise RevisionApplicationConflict("review_unavailable")
        candidate = review.snapshot.get("candidate_definition")
        if (
            not isinstance(candidate, dict)
            or definition_digest(record.tenant_id, record.agent_id, candidate)
            != record.candidate_definition_digest
        ):
            raise RevisionApplicationConflict("candidate_mismatch")

        holders = await self._projection.holders(record.agent_id)
        if len(holders) != 1:
            raise RevisionApplicationConflict("identity_not_unique")  # DEF-6 fails closed
        document = holders[0]
        if (
            document.get("tenant_id") != record.tenant_id
            or document.get("creator") != record.subject_id
            or not is_governed_definition(document)
        ):
            raise RevisionApplicationConflict("identity_mismatch")

        # REV-5: the projection precondition is checked BEFORE selection, so a detected
        # change never leaves an orphan selected revision behind an "unselected" claim.
        stored = await self._revisions.get(
            record.tenant_id, record.agent_id, record.target_revision
        )
        marker = document.get("definition_revision") or {}
        already = stored is not None and marker.get("revision_digest") == stored.revision_digest
        if not already and not self._predecessor_holds(record, document):
            raise RevisionApplicationConflict(
                "predecessor_changed",
                selected_revision_digest=None if stored is None else stored.revision_digest,
            )
        successor = await self._select(record, candidate, document, holders)
        applied = await self._project(record, successor, document)
        return {
            "agent_id": record.agent_id,
            "revision": successor.revision,
            "revision_digest": successor.revision_digest,
            "definition_digest": successor.definition_digest,
            "applied": applied,
        }

    async def _select(
        self,
        record: RevisionApplication,
        candidate: dict[str, Any],
        document: dict[str, Any],
        holders: list[dict[str, Any]],
    ) -> AgentDefinitionRevision:
        """The immutable successor: an existing identical record, or a new insert."""
        stored = await self._revisions.get(
            record.tenant_id, record.agent_id, record.target_revision
        )
        expected_predecessor = Predecessor(
            revision=record.predecessor.revision,
            revision_digest=record.predecessor.revision_digest,
        )
        if stored is not None:
            if stored.predecessor != expected_predecessor or stored.definition != candidate:
                raise RevisionApplicationConflict("successor_conflict")
            return stored
        predecessor = await self._revisions.get(
            record.tenant_id, record.agent_id, record.predecessor.revision
        )
        if predecessor is None:
            if record.predecessor.materialized or record.predecessor.revision != 1:
                raise RevisionApplicationConflict("predecessor_missing")
            # Materialize the verified creation baseline (never re-interpreted: the
            # verifier applies the creation record's own rule, and its digest must be
            # exactly the approved predecessor).
            verification = verify_baseline(record.tenant_id, record.agent_id, holders)
            if verification.status != "verified":
                raise RevisionApplicationConflict("predecessor_changed")
            baseline = baseline_revision(
                verification,
                document,
                recorded_at=self._clock(),
                correlation=RevisionCorrelation(operation_id=record.application_id),
            )
            if baseline.revision_digest != record.predecessor.revision_digest:
                raise RevisionApplicationConflict("predecessor_changed")
            predecessor, _ = await self._revisions.insert(baseline)
        if predecessor.revision_digest != record.predecessor.revision_digest:
            raise RevisionApplicationConflict("predecessor_changed")
        successor = AgentDefinitionRevision.build(
            tenant_id=record.tenant_id,
            agent_id=record.agent_id,
            revision=record.target_revision,
            origin="revision",
            predecessor=expected_predecessor,
            definition=candidate,
            correlation=RevisionCorrelation(
                operation_id=record.application_id, causation_id=record.approval_event_id
            ),
            recorded_at=self._clock(),
        )
        try:
            selected, _ = await self._revisions.insert(successor)
        except RevisionConflict:
            raise RevisionApplicationConflict("successor_conflict") from None
        return selected

    async def _project(
        self,
        record: RevisionApplication,
        successor: AgentDefinitionRevision,
        document: dict[str, Any],
    ) -> bool:
        """One compare-and-set against the exact predecessor; recognizes a prior apply."""
        marker = document.get("definition_revision") or {}
        if marker.get("revision_digest") == successor.revision_digest:
            self._verify_applied(document, successor)
            return False  # Already applied (crash after CAS, before its receipt).
        if record.predecessor.materialized:
            expected: dict[str, Any] = {
                "definition_revision.revision_digest": record.predecessor.revision_digest
            }
        else:
            expected = {"definition_revision": None}
        if not self._predecessor_holds(record, document):
            raise RevisionApplicationConflict(
                "predecessor_changed", selected_revision_digest=successor.revision_digest
            )
        now = self._clock()
        values = {field: successor.definition[field] for field in COMPOSED_FIELDS}
        values |= {
            "updated_at": now,
            "definition_revision": {
                "revision": successor.revision,
                "revision_digest": successor.revision_digest,
                "definition_digest": successor.definition_digest,
                "applied_at": now,
            },
        }
        # Fence on the exact observed predecessor: write time, creation evidence and every
        # revisable field as read (embedded values compare exactly as stored).
        expected["updated_at"] = document.get("updated_at")
        expected["store.request_digest"] = (document.get("store") or {}).get("request_digest")
        expected |= {field: document.get(field) for field in COMPOSED_FIELDS}
        if not await self._projection.compare_and_set(document, expected, values):
            # A concurrent writer moved the projection: re-derive on the next attempt.
            raise RevisionApplicationUnavailable("Projection changed concurrently")
        (applied,) = [
            d
            for d in await self._projection.holders(record.agent_id)
            if d.get("tenant_id") == record.tenant_id
        ]
        self._verify_applied(applied, successor)
        return True

    @staticmethod
    def _predecessor_holds(record: RevisionApplication, document: Mapping[str, Any]) -> bool:
        """The projection is exactly the approved predecessor (marker and content)."""
        marker = document.get("definition_revision") or {}
        if record.predecessor.materialized:
            marker_ok = marker.get("revision_digest") == record.predecessor.revision_digest
        else:
            marker_ok = not marker
        try:
            current = definition_digest(
                record.tenant_id, record.agent_id, canonical_definition(document)
            )
        except CanonicalizationError:
            return False
        return marker_ok and current == record.predecessor.definition_digest

    @staticmethod
    def _verify_applied(document: Mapping[str, Any], successor: AgentDefinitionRevision) -> None:
        if (
            definition_digest(
                successor.tenant_id, successor.agent_id, canonical_definition(document)
            )
            != successor.definition_digest
        ):
            raise RevisionApplicationCorrupt("Applied projection differs from its revision")


def _failure(
    code: str, *, retryable: bool, selected_revision_digest: str | None = None
) -> dict[str, Any]:
    meta: dict[str, Any] = {"code": code, "retryable": retryable}
    if selected_revision_digest is not None:
        meta["selected_revision_digest"] = selected_revision_digest
    return ToolResult(
        success=False, error="Revision application denied or unavailable", meta=meta
    ).model_dump()


def register_store_revise_approved(
    registry: SynapticRegistry, capability: RevisionStoreCapability
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        REVISE_SIGNAL,
        description="Select and apply exactly one approved Agent definition revision",
        version=REVISE_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": STORE_TOOL_SPEC.model_dump(),
        },
        component=SynapticBinding(
            REVISE_SIGNAL, REVISE_PROVIDER, capability.revise, REVISE_VERSION
        ),
    )


# --- The governed application coordinator --------------------------------------------------


class RevisionApplicationService:
    """Human-requested, Genesis-executed application of an effective approval."""

    def __init__(
        self,
        *,
        revisions: AgentDefinitionRevisionService,
        approvals: RevisionApprovalService,
        applications: MongoRevisionApplicationRepository,
        operations: ManagedOperationRepository,
        authority: UTUAuthority,
        exchange: SynapticExchange,
        audit: AuditSink,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_ms: int = 60_000,
    ) -> None:
        self._revisions = revisions
        self._approvals = approvals
        self._applications = applications
        self._operations = operations
        self._authority = authority
        self._exchange = exchange
        self._audit = audit
        self._clock = clock
        self._lease_ms = lease_ms
        self._owner_id = f"genesis-revision:{uuid4()}"

    async def apply(
        self, session: Any, agent_id: str, approval_id: str, *, policy: Policy | None = None
    ) -> RevisionApplication:
        auth = await self._revisions.authorize(session, policy)
        await self._revisions.require_target(auth, agent_id)
        approval = await self._approvals.owned(auth, agent_id, approval_id)
        claim = claim_id(auth.tenant_id, agent_id, approval.predecessor.revision_digest)
        identity = application_id(claim, approval)
        record = await self._applications.get(auth.tenant_id, claim)
        if (
            record is not None
            and record.application_id == identity
            and record.state
            in (
                "completed",
                "failed",
            )
        ):
            return record  # Terminal: replay the outcome.
        if (
            record is not None
            and record.application_id != identity
            and not (record.state == "failed" and record.selected_revision_digest is None)
        ):
            raise RevisionApplicationConflict("predecessor_claimed")

        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(
                auth.tenant_id, auth.subject_id, approval.executor_id, session.session_id
            )
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap revision application denied")
        revise = await self._authority.attenuate(principal, REVISE_SCOPE)

        if approval.consumed_by != identity:
            # Before the claim: withdrawal, expiry and staleness all still apply.
            try:
                await self._approvals.check_effective(auth, approval)
            except RevisionApprovalError as exc:
                if (
                    record is not None
                    and record.application_id == identity
                    and record.state == "claimed"
                ):
                    # A claim whose approval never got consumed: release the predecessor.
                    await self._applications.transition(
                        record,
                        expected=("claimed",),
                        changes={
                            "state": "failed",
                            "issue_codes": (exc.code,),
                            "updated_at": self._clock(),
                        },
                    )
                raise
            now = self._clock()
            record = await self._applications.claim(
                RevisionApplication(
                    claim_id=claim,
                    application_id=identity,
                    tenant_id=auth.tenant_id,
                    subject_id=auth.subject_id,
                    agent_id=agent_id,
                    executor_id=approval.executor_id,
                    approval_id=approval.approval_id,
                    approval_event_id=approval.approval_event_id or "",
                    review_id=approval.review_id,
                    review_digest=approval.review_digest,
                    candidate_definition_digest=approval.candidate_definition_digest,
                    predecessor=approval.predecessor,
                    target_revision=approval.predecessor.revision + 1,
                    state="claimed",
                    authorization=permit_provenance(revise.permit, REVISE_SIGNAL),
                    claimed_at=now,
                    updated_at=now,
                )
            )
            if record.application_id != identity:
                raise RevisionApplicationConflict("predecessor_claimed")
            try:
                approval = await self._approvals.consume(approval, identity)
            except (
                RevisionApprovalWithdrawn,
                RevisionApprovalConsumed,
                RevisionApprovalExpired,
            ) as exc:
                # Withdrawal committed first: release the claim (nothing was selected).
                await self._applications.transition(
                    record,
                    expected=("claimed",),
                    changes={
                        "state": "failed",
                        "issue_codes": (exc.code,),
                        "updated_at": self._clock(),
                    },
                )
                raise
        elif record is None or record.application_id != identity:
            # This approval was spent by its own application, whose claim was released
            # (failed before selecting) and is now held by, or open to, another one.
            raise RevisionApplicationConflict("claim_released")
        # After the claim: withdrawal is refused and expiry no longer applies.
        return await self._execute(record, revise)

    async def status(
        self, session: Any, agent_id: str, approval_id: str, *, policy: Policy | None = None
    ) -> RevisionApplication:
        auth = await self._revisions.authorize(session, policy)
        await self._revisions.require_target(auth, agent_id)
        approval = await self._approvals.owned(auth, agent_id, approval_id)
        claim = claim_id(auth.tenant_id, agent_id, approval.predecessor.revision_digest)
        record = await self._applications.get(auth.tenant_id, claim)
        if record is None or record.application_id != application_id(claim, approval):
            raise RevisionApplicationNotFound("Application not found")
        return record

    async def _execute(self, record: RevisionApplication, revise: Any) -> RevisionApplication:
        operation_id = UUID(record.application_id)
        managed = await self._open(operation_id)
        state = managed.snapshot.state
        if state.terminal:
            return await self._settle_terminal(record, managed)
        if state == OperationState.RUNNING and self._expired(managed):
            # A lease expiry is not evidence of failure: reconcile to retry-pending only.
            managed = await self._reconcile(managed)
        elif state in {OperationState.RUNNING, OperationState.CANCEL_REQUESTED}:
            raise RevisionApplicationInProgress("Another attempt holds this application")
        managed = await self._activate(managed)
        attempt = managed.snapshot.attempts[-1]
        client = self._exchange.bind(revise)
        finishing = False
        try:
            await self._authority.check_permit(revise.permit)
            envelope = await client.request(
                REVISE_SIGNAL,
                {
                    "resource_type": "agent",
                    "claim_id": record.claim_id,
                    "application_id": record.application_id,
                },
                version=REVISE_VERSION,
                correlation_id=record.application_id,
                causation_id=record.approval_event_id,
            )
            if not isinstance(envelope, dict) or not envelope.get("success"):
                meta = (envelope or {}).get("meta") or {}
                code = str(meta.get("code") or "revise.failed")
                retry = meta.get("retryable") is True
                finishing = True
                await self._finish(managed, attempt, AttemptState.FAILED, reason=code, retry=retry)
                selected = meta.get("selected_revision_digest")
                if selected and record.state == "claimed":
                    # A successor exists: the claim stays held and inspectable, never
                    # released as unselected (operator inspection required).
                    record = await self._applications.transition(
                        record,
                        expected=("claimed",),
                        changes={
                            "state": "selected",
                            "selected_revision_digest": selected,
                            "updated_at": self._clock(),
                        },
                    )
                if retry or record.selected_revision_digest is not None:
                    return record  # Uncertain or already selected: repeat the request.
                return await self._applications.transition(
                    record,
                    expected=("claimed",),
                    changes={
                        "state": "failed",
                        "issue_codes": (code,),
                        "updated_at": self._clock(),
                    },
                )
            result = envelope["result"]
            record = await self._receipts(record, result)
            finishing = True
            await self._finish(managed, attempt, AttemptState.SUCCEEDED)
            return await self._applications.transition(
                record,
                expected=("applied",),
                changes={
                    "state": "completed",
                    "completed_at": self._clock(),
                    "updated_at": self._clock(),
                },
            )
        except BaseException:
            if not finishing:
                # Every Store step is idempotent: never claim an outcome on an error.
                await self._finish(
                    managed, attempt, AttemptState.FAILED, reason="error", retry=True
                )
            raise
        finally:
            client.revoke()
            await client.drain()

    async def _receipts(
        self, record: RevisionApplication, result: dict[str, Any]
    ) -> RevisionApplication:
        """Durable receipts from the Store result, each recorded once (idempotent)."""
        digest = result["revision_digest"]
        if record.selected_revision_digest not in (None, digest):
            raise RevisionApplicationCorrupt("A different revision was selected for this claim")
        if record.state == "claimed":
            record = await self._applications.transition(
                record,
                expected=("claimed",),
                changes={
                    "state": "selected",
                    "selected_revision_digest": digest,
                    "updated_at": self._clock(),
                },
            )
        selection = self._event_id(record, SELECTED)
        await self._record(record, SELECTED, selection, result)
        if record.selection_event_id is None:
            record = await self._applications.transition(
                record, expected=("selected",), changes={"selection_event_id": selection}
            )
        if record.state == "selected":
            record = await self._applications.transition(
                record,
                expected=("selected",),
                changes={
                    "state": "applied",
                    "applied_at": self._clock(),
                    "updated_at": self._clock(),
                },
            )
        application = self._event_id(record, APPLIED)
        await self._record(record, APPLIED, application, result)
        if record.application_event_id is None:
            record = await self._applications.transition(
                record, expected=("applied",), changes={"application_event_id": application}
            )
        return record

    @staticmethod
    def _event_id(record: RevisionApplication, event_type: str) -> str:
        return stable_event_id(
            correlation_id=record.application_id, event_type=event_type, step=record.claim_id
        )

    async def _record(
        self,
        record: RevisionApplication,
        event_type: EventType,
        event_id: str,
        result: dict[str, Any],
    ) -> None:
        event = LogEvent(
            event_id=event_id,
            event_type=event_type,
            outcome="succeeded",
            subject=LogSubject(resource_type="agent", resource_id=record.agent_id),
            target=LogTarget(signal=REVISE_SIGNAL, version=REVISE_VERSION),
            correlation=LogCorrelation(
                correlation_id=record.application_id,
                causation_id=record.approval_event_id,
                operation_id=UUID(record.application_id),
            ),
            metadata={
                "event_version": 1,
                "application_id": record.application_id,
                "approval_id": record.approval_id,
                "agent_id": record.agent_id,
                "review_digest": record.review_digest,
                "revision": result["revision"],
                "revision_digest": result["revision_digest"],
                "definition_digest": result["definition_digest"],
                "predecessor_revision": record.predecessor.revision,
                "predecessor_revision_digest": record.predecessor.revision_digest,
            },
        )
        try:
            await self._audit.append(event, authorization=record.authorization)
        except LogConflict:
            stored = await self._audit.get(record.tenant_id, event_id)
            if stored is None or stored.metadata != event.metadata:
                raise
        except LogError as exc:
            raise RevisionApplicationUnavailable("Application evidence is unavailable") from exc

    # --- OPS V2 managed operation lifecycle (the existing engine) -------------------------

    async def _open(self, operation_id: UUID) -> ManagedOperation:
        snapshot = OperationRecord.create(
            kind=OPERATION_KIND,
            producer=OPERATION_PRODUCER,
            at=self._clock(),
            operation_id=operation_id,
            correlation_id=str(operation_id),
            causation_id=str(operation_id),
        )
        try:
            return await self._operations.create(snapshot)
        except DuplicateOperation:
            return await self._operations.inspect(operation_id)

    async def _activate(self, managed: ManagedOperation) -> ManagedOperation:
        try:
            return await self._operations.activate(
                managed.snapshot.operation_id,
                expected_revision=managed.snapshot.revision,
                expected_generation=managed.execution.generation,
                attempt_id=uuid4(),
                owner_id=self._owner_id,
                lease_ms=self._lease_ms,
                at=self._clock(),
                command_id=uuid4(),
            )
        except (
            OperationRevisionConflict,
            ObsoleteFencingToken,
            OwnershipConflict,
            AttemptConflict,
        ):
            raise RevisionApplicationInProgress(
                "A concurrent attempt owns this application"
            ) from None

    async def _reconcile(self, managed: ManagedOperation) -> ManagedOperation:
        operation_id = managed.snapshot.operation_id
        attempt_id = managed.snapshot.attempts[-1].attempt_id
        try:
            taken = await self._operations.takeover(
                operation_id,
                expected_revision=managed.snapshot.revision,
                expected_generation=managed.execution.generation,
                attempt_id=attempt_id,
                owner_id=self._owner_id,
                lease_ms=self._lease_ms,
            )
            return await self._operations.resolve_reconciled(
                operation_id,
                expected_revision=taken.snapshot.revision,
                attempt_id=attempt_id,
                owner_id=self._owner_id,
                generation=taken.execution.generation,
                reason="interrupted revision application reconciled",
                at=self._clock(),
                command_id=uuid4(),
            )
        except (
            OperationRevisionConflict,
            ObsoleteFencingToken,
            OwnershipConflict,
            AttemptConflict,
            LeaseExpired,
            OwnershipReleased,
        ):
            raise RevisionApplicationInProgress("Another attempt is reconciling") from None

    def _expired(self, managed: ManagedOperation) -> bool:
        epoch = managed.execution
        return epoch.status == EpochStatus.RELEASED or (
            epoch.lease_expires_at is not None and epoch.lease_expires_at <= self._clock()
        )

    async def _finish(
        self,
        managed: ManagedOperation,
        attempt: Any,
        outcome: AttemptState,
        *,
        reason: str | None = None,
        retry: bool = False,
    ) -> None:
        snapshot = finish_attempt(
            managed.snapshot,
            attempt_id=attempt.attempt_id,
            outcome=outcome,
            at=self._clock(),
            reason=reason,
            retry=retry,
        )
        await self._operations.commit_outcome(
            snapshot,
            expected_revision=managed.snapshot.revision,
            attempt_id=attempt.attempt_id,
            owner_id=self._owner_id,
            generation=managed.execution.generation,
            command_id=uuid4(),
        )

    async def _settle_terminal(
        self, record: RevisionApplication, managed: ManagedOperation
    ) -> RevisionApplication:
        """A terminal operation whose record is not: the record is completed only from
        the authoritative revision record and projection (re-run idempotently)."""
        if managed.snapshot.state == OperationState.SUCCEEDED:
            if record.state == "completed":
                return record
            if record.state == "applied" and record.application_event_id is not None:
                # Crash after the outcome commit, before the receipt: complete it.
                return await self._applications.transition(
                    record,
                    expected=("applied",),
                    changes={
                        "state": "completed",
                        "completed_at": self._clock(),
                        "updated_at": self._clock(),
                    },
                )
        if record.state == "failed":
            return record
        raise RevisionApplicationUnavailable(
            "Operation and claim disagree; inspect before retrying"
        )
