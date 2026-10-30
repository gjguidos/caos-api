"""Governed manual creation of one approved Agent through Genesis (G3.4).

Effective human approval → independent creation authorization → Genesis → Store →
sleeping Agent. An approval is evidence of reviewed intent, not authority: every
request re-establishes current authority, re-reviews the exact approved definition
and runs Genesis under two separately scoped permits of the reviewed executor
(validation-only, and ``store.create.approved`` only). Store independently re-verifies
the approval at the write. Nothing here activates an Agent, provisions a grant or
calls an LLM.

Identity: one approval maps to one creation request (it is consumed once), one Genesis
operation and one Store idempotency scope, hence one deterministic Agent ID. Retries of
the same request converge; any partial failure leaves a recoverable, non-final state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.agents.creation_review.models import (
    ApprovalError,
    ApprovalExpired,
    ApprovalNotEffective,
    ApprovalRevoked,
    CorruptApproval,
    CreationUnavailable,
    IdempotencyConflict,
    IdempotencyKey,
    ReviewStale,
)
from src.domains.agentverse.agents.genesis import GenesisProvisioner, genesis_operation_id
from src.domains.agentverse.agents.genesis.models import (
    ApprovedCreation,
    GenesisAuditError,
    GenesisOperationInProgress,
    GenesisProvisionRequest,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.audit.models import (
    LogCorrelation,
    LogEvent,
    LogSubject,
    LogTarget,
    stable_event_id,
)
from src.domains.agentverse.audit.repository import LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.security.validation_gateway import (
    VALIDATION_SCOPE,
    AgentDraftValidationGateway,
    AgentDraftValidationRequest,
    ValidationGatewayBinding,
)
from src.domains.agentverse.tools.base import ToolResult
from src.domains.agentverse.tools.store_tool import StoreTool
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_APPROVED_SIGNAL,
    STORE_CREATE_APPROVED_VERSION,
    STORE_PROVIDER,
    STORE_TOOL_SPEC,
)
from src.domains.agentverse.tools.store_tool.store.agent_store import scope_digest

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository
    from src.domains.agentverse.agents.creation_review.service import DraftReviewApprovalService
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
    from src.domains.agentverse.ncsc.models import Synapse

CREATION_BINDING = CapabilityBinding(
    STORE_CREATE_APPROVED_SIGNAL, STORE_PROVIDER, STORE_CREATE_APPROVED_VERSION
)
CREATION_SCOPE = CapabilityScope(
    "agent.creation_draft.create", CREATION_BINDING, frozenset({CREATION_BINDING})
)
REQUEST_CONTRACT = "caos.agent_creation_request"
RESULT_CONTRACT = "caos.agent_creation"
CREATIONS_COLLECTION = "coll_agent_creation_operations"
AUTHORIZED = "agent.creation.authorized"
COMPLETED = "agent.creation.completed"
STORE_COMPLETED = "genesis.store.completed"

CreationState = Literal["accepted", "provisioning", "completed", "failed"]


def approved_key(approval_id: str) -> str:
    """Store idempotency key of an approved creation: one Agent per approval, ever."""
    return f"approved:{approval_id}"


def target_agent_id(tenant_id: str, subject_id: str, executor_id: str, approval_id: str) -> str:
    """The Agent ID Store derives for this approval (same rule as ``AgentDefinitionStore``)."""
    scope = scope_digest(
        tenant_id=tenant_id,
        subject_id=subject_id,
        principal_id=executor_id,
        key=approved_key(approval_id),
    )
    return str(UUID(scope[:32]))


def creation_identity(tenant_id: str, subject_id: str, idempotency_key: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-creation:"
            + sha256_hex(
                {"tenant_id": tenant_id, "subject_id": subject_id, "key": idempotency_key}
            ),
        )
    )


class AgentCreationRequest(BaseModel):
    """Only a reference to an approval and the digest the client reviewed. Definitions,
    identities, executors, grants, backends and signals are not expressible."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_creation_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    approval_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expected_review_digest: Digest

    def digest(self, source_agent_id: str) -> str:
        return sha256_hex(
            {
                "source_agent_id": source_agent_id,
                "approval_id": self.approval_id,
                "expected_review_digest": self.expected_review_digest,
            }
        )


class AgentCreationRecord(BaseModel):
    """Durable creation operation. Identity fields are immutable after insert; only
    state, evidence and outcome fields change, by compare-and-set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    creation_id: str
    tenant_id: str
    subject_id: str
    source_agent_id: str
    executor_id: str
    approval_id: str
    review_digest: Digest
    request_digest: Digest
    operation_id: str
    target_agent_id: str
    state: CreationState
    created_at: datetime
    updated_at: datetime
    # Decision-time creation authority, reused so retried evidence is identical.
    authorization: AuthorizationProvenance
    authorized_event_id: str | None = None
    completion_authorization: AuthorizationProvenance | None = None
    completion_event_id: str | None = None
    agent_id: str | None = None
    store_request_digest: Digest | None = None
    completed_at: datetime | None = None
    issue_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.authorization.tenant_id, self.authorization.subject_id) != (
            self.tenant_id,
            self.subject_id,
        ) or self.authorization.principal_id != self.executor_id:
            raise ValueError("Creation authority differs from its requester")
        if self.state in ("provisioning", "completed") and self.authorized_event_id is None:
            raise ValueError("Creation is dispatched only after its authorization evidence")
        completed = (
            self.agent_id,
            self.store_request_digest,
            self.completion_event_id,
            self.completed_at,
        )
        if (self.state == "completed") != all(value is not None for value in completed):
            raise ValueError("Completion requires its Store and audit evidence")
        if self.agent_id is not None and self.agent_id != self.target_agent_id:
            raise ValueError("Created Agent differs from the stable target identity")
        if (self.state == "failed") != bool(self.issue_codes):
            raise ValueError("Only a failed creation has issue codes")
        return self

    def public(self) -> dict[str, Any]:
        completed = self.state == "completed"
        return {
            "contract": RESULT_CONTRACT,
            "schema_version": 1,
            "creation_id": self.creation_id,
            "state": self.state,
            "approval_id": self.approval_id,
            "source_agent_id": self.source_agent_id,
            "operation_id": self.operation_id,
            "agent_id": self.agent_id if completed else None,
            "issue_codes": list(self.issue_codes),
            "retryable": self.state in ("accepted", "provisioning"),
            "authorizes": {"activation": False},
        }


_MUTABLE = frozenset(
    {
        "state",
        "updated_at",
        "authorized_event_id",
        "completion_authorization",
        "completion_event_id",
        "agent_id",
        "store_request_digest",
        "completed_at",
        "issue_codes",
    }
)


class MongoAgentCreationRepository:
    """``_id`` is the creation ID (unique per requester and idempotency key); state
    changes are compare-and-set transitions. Same conventions as approvals."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = CREATIONS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection_name = collection_name

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection_name)
        except PyMongoError as exc:
            raise CreationUnavailable("Creation store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise CreationUnavailable("Creation persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    @staticmethod
    def _load(document: dict[str, Any]) -> AgentCreationRecord:
        try:
            data = dict(document)
            identity = data.pop("_id")
            record = AgentCreationRecord.model_validate(data)
            if identity != record.creation_id:
                raise ValueError("Storage and creation identities disagree")
            return record
        except (ValueError, TypeError, KeyError) as exc:
            raise CorruptApproval("Invalid stored creation operation") from exc

    async def insert(self, record: AgentCreationRecord) -> tuple[AgentCreationRecord, bool]:
        collection = await self._collection()
        try:
            await collection.insert_one(
                {"_id": record.creation_id, **record.model_dump(mode="json")}
            )
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise CreationUnavailable("Creation write outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.creation_id)
        if stored is None:
            raise CreationUnavailable("Creation write outcome is unknown")
        if stored.request_digest != record.request_digest:
            raise IdempotencyConflict("Idempotency key already used for another request")
        return stored, False

    async def get(self, tenant_id: str, creation_id: str) -> AgentCreationRecord | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": creation_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise CreationUnavailable("Creation store is unavailable") from exc
        return None if document is None else self._load(document)

    async def transition(
        self,
        record: AgentCreationRecord,
        *,
        expected: tuple[CreationState, ...],
        changes: dict[str, Any],
        unset: tuple[str, ...] = (),
    ) -> AgentCreationRecord:
        if not changes or not set(changes) <= _MUTABLE:
            raise ValueError("Only creation state fields can change")
        updated = AgentCreationRecord.model_validate({**record.model_dump(), **changes})
        values = {key: updated.model_dump(mode="json")[key] for key in changes}
        collection = await self._collection()
        try:
            await collection.update_one(
                {
                    "_id": record.creation_id,
                    "tenant_id": record.tenant_id,
                    "approval_id": record.approval_id,
                    "state": {"$in": list(expected)},
                    **{field: None for field in unset},
                },
                {"$set": values},
            )
        except PyMongoError as exc:
            raise CreationUnavailable("Creation transition outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.creation_id)
        if stored is None:
            raise CorruptApproval("Creation disappeared during a transition")
        return stored


class StoreApprovedCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: Literal["agent"]
    approval_id: str = Field(min_length=1, max_length=128)
    review_digest: Digest
    creation_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]


class ApprovedStoreCapability:
    """``store.create.approved``: write exactly one effective approval's candidate.

    Executable only through the reviewed creation scope (never by an unscoped or other
    permit). It independently re-verifies the approval for the permit's tenant and
    subject, its executor, digest and consuming creation, and writes the *approved*
    candidate (the submitted one must equal it). Identity is derived by Store from the
    approval, so retries converge and an approval can never create a second Agent.
    """

    def __init__(self, *, tool: StoreTool, approvals: DraftReviewApprovalService) -> None:
        self._tool = tool
        self._approvals = approvals

    async def create(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        context = await self._tool._write_context(
            synapse, client, signal=STORE_CREATE_APPROVED_SIGNAL
        )
        permit = client.execution_permit
        if (
            not isinstance(permit, ExecutionPermit)
            or permit.scope != CREATION_SCOPE
            or not permit.binding_chain
            or permit.binding_chain[-1] != CREATION_BINDING
            or synapse.entry_operation != CREATION_SCOPE.operation
            or synapse.provider_version != STORE_CREATE_APPROVED_VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError("store.create.approved requires its creation scope")
        try:
            request = StoreApprovedCreateRequest.model_validate(synapse.payload)
        except ValidationError:
            return self._failure("store.invalid_request", retryable=False)
        try:
            approval = await self._approvals.effective(
                permit.key.tenant_id, permit.key.user_id, request.approval_id
            )
        except ApprovalError as exc:
            return self._failure(exc.code, retryable=exc.retryable)
        if (
            approval.executor_id != permit.key.agent_id
            or approval.review_digest != request.review_digest
            or approval.consumed_by != request.creation_id
            or approval.snapshot.get("candidate") != request.payload
        ):
            return self._failure("approval.mismatch", retryable=False)
        return await self._tool._create(
            "agent",
            approval.snapshot["candidate"],
            idempotency_key=approved_key(approval.approval_id),
            context=context,
        )

    @staticmethod
    def _failure(code: str, *, retryable: bool) -> dict[str, Any]:
        return ToolResult(
            success=False,
            error="Approved creation denied or unavailable",
            meta={"code": code, "retryable": retryable},
        ).model_dump()


def register_store_create_approved(
    registry: SynapticRegistry, capability: ApprovedStoreCapability
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        STORE_CREATE_APPROVED_SIGNAL,
        description="Idempotent creation of exactly one effective human-approved Agent",
        version=STORE_CREATE_APPROVED_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": STORE_TOOL_SPEC.model_dump(),
        },
        component=SynapticBinding(
            STORE_CREATE_APPROVED_SIGNAL,
            STORE_PROVIDER,
            capability.create,
            STORE_CREATE_APPROVED_VERSION,
        ),
    )


class AgentCreationService:
    """Server-owned creation gateway for reviewed (tenant, user, source Agent) bindings."""

    def __init__(
        self,
        *,
        bindings: tuple[ValidationGatewayBinding, ...],
        authority: UTUAuthority,
        exchange: SynapticExchange,
        gateway: AgentDraftValidationGateway,
        approvals: DraftReviewApprovalService,
        provisioner: GenesisProvisioner,
        repository: MongoAgentCreationRepository,
        audit: AuditSink,
        read_definition: Callable[[str], Awaitable[dict[str, Any] | None]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        entries = {(b.tenant_id, b.subject_id, b.source_agent_id): b for b in bindings}
        if len(entries) != len(bindings):
            raise ValueError("Duplicate creation gateway binding")
        self._bindings: Mapping[tuple[str, str, str], ValidationGatewayBinding] = MappingProxyType(
            entries
        )
        self._authority = authority
        self._exchange = exchange
        self._gateway = gateway
        self._approvals = approvals
        self._provisioner = provisioner
        self._repository = repository
        self._audit = audit
        self._read_definition = read_definition
        self._clock = clock

    async def create(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        request: AgentCreationRequest,
        idempotency_key: str,
        *,
        authorize_creator: Callable[[User], Awaitable[None]],
    ) -> AgentCreationRecord:
        # 1-2. Authenticated human and trusted tenant/session context.
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        IdempotencyKeyModel.model_validate({"key": idempotency_key})
        tenant_id, subject_id = session.tenant_id, session.user_id
        binding = self._bindings.get((tenant_id, subject_id, source_agent_id))
        if binding is None:
            raise EventPermissionError("Creation gateway is not approved")
        # 3-4. Current requester and current agent.write / agent_access.can_create.
        await authorize_creator(await self._requester(session, binding))
        creation_id = creation_identity(tenant_id, subject_id, idempotency_key)
        request_digest = request.digest(source_agent_id)
        record = await self._repository.get(tenant_id, creation_id)
        if record is not None and record.request_digest != request_digest:
            raise IdempotencyConflict("Idempotency key already used for another request")
        if record is not None and record.state in ("completed", "failed"):
            return record  # Terminal: replay the outcome, nothing re-executes.

        # 6. Ownership is the subject's; tenant isolation by the repository query.
        approval = await self._approvals.owned(tenant_id, subject_id, request.approval_id)
        if (
            approval.source_agent_id != source_agent_id
            or approval.executor_id != binding.executor_id
            or approval.review_digest != request.expected_review_digest
        ):
            raise ReviewStale("The approval does not match this creation request")
        target = target_agent_id(tenant_id, subject_id, binding.executor_id, approval.approval_id)
        stored = record is not None and await self._stored(record)
        recheck: Callable[[], Awaitable[User]] | None = None
        if not stored:
            # 5, 7. Effective (owned, evidenced, unexpired, unrevoked) approval.
            try:
                approval = await self._approvals.effective(
                    tenant_id, subject_id, approval.approval_id
                )
            except (ApprovalRevoked, ApprovalExpired) as exc:
                # Monotonic: this creation can never proceed. Settle it only when no
                # Genesis attempt can still write and Store holds nothing.
                if (
                    record is not None
                    and await self._provisioner.quiescent(UUID(record.operation_id))
                    and not await self._stored(record)
                ):
                    return await self._repository.transition(
                        record,
                        expected=("accepted", "provisioning"),
                        changes={
                            "state": "failed",
                            "issue_codes": (exc.code,),
                            "updated_at": self._clock(),
                        },
                    )
                raise
            # 3, 9-11. Current source access, re-validation, re-composition, current
            # dependency fingerprints and policy reproduce the approved digest exactly.
            review = await self._gateway.review(
                session,
                source_agent_id,
                AgentDraftValidationRequest(
                    contract="caos.agent_creation_draft",
                    schema_version=1,
                    resource_type="agent",
                    creation=approval.snapshot["normalized"],
                ),
            )
            if not review.eligible or review.digest != approval.review_digest:
                raise ReviewStale("The approved definition or its authority changed")
            recheck = review.recheck
        # Otherwise the Agent already exists and only completion evidence remains: a
        # later revocation never undoes or blocks recording an effected creation.

        # 12. Authority for exactly the reviewed operation, separately scoped.
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(tenant_id, subject_id, binding.executor_id, session.session_id)
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap creation denied")
        validation = await self._authority.attenuate(principal, VALIDATION_SCOPE)
        creation = await self._authority.attenuate(principal, CREATION_SCOPE)
        genesis_request = GenesisProvisionRequest(
            approved=ApprovedCreation(
                approval_id=approval.approval_id,
                review_digest=approval.review_digest,
                creation_id=creation_id,
            ),
            creation=approval.snapshot["normalized"],
        )
        operation_id = str(genesis_operation_id(validation.permit, genesis_request))

        if record is None:
            # One approval, one creation request: retries of this request only.
            await self._approvals.consume(approval, creation_id)
            now = self._clock()
            record, _ = await self._repository.insert(
                AgentCreationRecord(
                    schema_version=1,
                    creation_id=creation_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_agent_id=source_agent_id,
                    executor_id=binding.executor_id,
                    approval_id=approval.approval_id,
                    review_digest=approval.review_digest,
                    request_digest=request_digest,
                    operation_id=operation_id,
                    target_agent_id=target,
                    state="accepted",
                    created_at=now,
                    updated_at=now,
                    authorization=permit_provenance(creation.permit, STORE_CREATE_APPROVED_SIGNAL),
                )
            )
        if (record.operation_id, record.target_agent_id) != (operation_id, target):
            raise CorruptApproval("Creation identity differs from its approval")
        if not stored and approval.consumed_by != creation_id:
            await self._approvals.consume(approval, creation_id)

        # Mandatory authorization evidence before Genesis is dispatched.
        if record.authorized_event_id is None:
            event_id = self._event_id(record, AUTHORIZED)
            await self._record(
                record,
                AUTHORIZED,
                event_id,
                record.authorization,
                causation_id=approval.approval_event_id,
            )
            record = await self._repository.transition(
                record,
                expected=("accepted",),
                changes={"authorized_event_id": event_id, "updated_at": self._clock()},
                unset=("authorized_event_id",),
            )
        if recheck is not None:
            await recheck()
        await self._authority.check_permit(creation.permit)
        if record.state == "accepted":
            record = await self._repository.transition(
                record,
                expected=("accepted",),
                changes={"state": "provisioning", "updated_at": self._clock()},
            )

        validation_client = self._exchange.bind(validation)
        store_client = self._exchange.bind(creation)
        try:
            result = await self._provisioner.provision(
                validation_client, genesis_request, store_client=store_client
            )
        except (GenesisOperationInProgress, GenesisAuditError):
            # Uncertain or in progress: never reported as a failure. Repeat the request.
            return record
        finally:
            for client in (validation_client, store_client):
                client.revoke()
                await client.drain()

        if result.status != "provisioned":
            if result.retryable:
                return record
            return await self._repository.transition(
                record,
                expected=("provisioning",),
                changes={
                    "state": "failed",
                    "issue_codes": result.issue_codes or (f"{result.stage}.{result.status}",),
                    "updated_at": self._clock(),
                },
            )
        return await self._complete(record, genesis_request, creation.permit)

    async def _complete(
        self,
        record: AgentCreationRecord,
        genesis_request: GenesisProvisionRequest,
        permit: ExecutionPermit,
    ) -> AgentCreationRecord:
        """Completion is established from authoritative Store evidence (the persisted
        definition and its Genesis store evidence), never from a response alone."""
        store_evidence = await self._store_evidence(record, genesis_request)
        if store_evidence is None or not await self._stored(record):
            raise CreationUnavailable("Store evidence is not confirmed; repeat the request")
        request_digest = store_evidence.metadata["request_digest"]
        if record.completion_authorization is None:
            record = await self._repository.transition(
                record,
                expected=("provisioning",),
                changes={
                    "completion_authorization": permit_provenance(
                        permit, STORE_CREATE_APPROVED_SIGNAL
                    ),
                    "updated_at": self._clock(),
                },
                unset=("completion_authorization",),
            )
        event_id = self._event_id(record, COMPLETED)
        await self._record(
            record,
            COMPLETED,
            event_id,
            record.completion_authorization,
            causation_id=store_evidence.event_id,
            store_request_digest=request_digest,
            created=store_evidence.metadata["created"],
        )
        now = self._clock()
        stored = await self._repository.transition(
            record,
            expected=("provisioning",),
            changes={
                "state": "completed",
                "agent_id": record.target_agent_id,
                "store_request_digest": request_digest,
                "completion_event_id": event_id,
                "completed_at": now,
                "updated_at": now,
            },
        )
        if stored.state != "completed":
            raise CreationUnavailable("Completion did not take effect; repeat the request")
        return stored

    async def _requester(
        self, session: VerifiedAgentSession, binding: ValidationGatewayBinding
    ) -> User:
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(
                session.tenant_id, session.user_id, binding.executor_id, session.session_id
            )
        )
        return await self._authority.resource_requester(principal.permit)

    async def _stored(self, record: AgentCreationRecord) -> bool:
        """True only for the exact Store-created definition of this approval."""
        document = await self._read_definition(record.target_agent_id)
        if document is None:
            return False
        store = document.get("store") or {}
        expected_scope = scope_digest(
            tenant_id=record.tenant_id,
            subject_id=record.subject_id,
            principal_id=record.executor_id,
            key=approved_key(record.approval_id),
        )
        if (
            document.get("id") != record.target_agent_id
            or document.get("tenant_id") != record.tenant_id
            or document.get("creator") != record.subject_id
            or store.get("idempotency_scope") != expected_scope
        ):
            raise ApprovalNotEffective("Agent identity is held by another definition")
        return True

    async def _store_evidence(
        self, record: AgentCreationRecord, genesis_request: GenesisProvisionRequest
    ) -> Any:
        event_id = stable_event_id(
            correlation_id=record.operation_id,
            event_type=STORE_COMPLETED,
            step=genesis_request.step,
        )
        try:
            evidence = await self._audit.get(record.tenant_id, event_id)
        except LogError as exc:
            raise CreationUnavailable("Store evidence is unavailable") from exc
        document = await self._read_definition(record.target_agent_id) or {}
        if (
            evidence is None
            or evidence.subject.resource_id != record.target_agent_id
            or evidence.metadata.get("request_digest")
            != (document.get("store") or {}).get("request_digest")
        ):
            return None
        return evidence

    @staticmethod
    def _event_id(record: AgentCreationRecord, event_type: str) -> str:
        return stable_event_id(
            correlation_id=record.operation_id, event_type=event_type, step=record.creation_id
        )

    async def _record(
        self,
        record: AgentCreationRecord,
        event_type: str,
        event_id: str,
        authorization: AuthorizationProvenance | None,
        *,
        causation_id: str | None,
        store_request_digest: str | None = None,
        created: bool | None = None,
    ) -> None:
        if authorization is None:
            raise CreationUnavailable("Creation evidence has no attributed authority")
        event = LogEvent(
            event_id=event_id,
            event_type=event_type,
            outcome="succeeded",
            subject=LogSubject(resource_type="agent", resource_id=record.target_agent_id),
            target=LogTarget(
                signal=STORE_CREATE_APPROVED_SIGNAL, version=STORE_CREATE_APPROVED_VERSION
            ),
            correlation=LogCorrelation(
                correlation_id=record.operation_id,
                causation_id=causation_id,
                operation_id=UUID(record.operation_id),
            ),
            metadata={
                "event_version": 1,
                "creation_id": record.creation_id,
                "approval_id": record.approval_id,
                "review_digest": record.review_digest,
                "source_agent_id": record.source_agent_id,
                "target_agent_id": record.target_agent_id,
                "store_request_digest": store_request_digest,
                "created": created,
            },
        )
        try:
            await self._audit.append(event, authorization=authorization)
        except LogError as exc:
            raise CreationUnavailable(
                "Creation evidence is unavailable; repeat the request"
            ) from exc


class IdempotencyKeyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: IdempotencyKey
