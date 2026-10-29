"""Genesis provisioning: the minimal trusted coordinator that creates a policy-pinned agent.

``data.validation`` → Seshat → ``AgentComposer`` → Seshat → ``store.create.provisioned``
→ Seshat, under one OPS V2 managed operation.

Trust: the caller supplies a CPBX-issued client bound to the Genesis principal's
verified UTU permit; the coordinator never constructs a permit, never bypasses CPBX
(both capabilities are invoked through the client, so grants are enforced), and derives
every audit attribution from that client's permit. Only Genesis's two granted signals
are used; no grant or trusted line is added.

Identity and retries: the operation ID is derived from tenant, principal, subject, slot
and the exact creation input, so an identical request is the same operation. OPS V2
managed fencing allows one executing attempt at a time. Every effect is idempotent
(read-only validation, Store's pinned slot scope, Seshat's stable event IDs), so a
retry-pending operation re-executes safely. Successful steps have one event per
operation; rejections/failures have one event per attempt.

Not atomic: Store and Seshat are separate writes. An operation never reaches
``succeeded`` without its store evidence. If the process dies mid-attempt, the next
identical request takes the expired attempt over in reconcile mode, resolves it to
retry-pending through OPS V2 ``resolve_reconciled`` (never to success), and runs a fresh
attempt: Store replays idempotently and only missing evidence is appended under its
stable event ID. A live lease is reported as ``GenesisOperationInProgress``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
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
from src.base.operations.v2.repository import DuplicateOperation, RevisionConflict
from src.base.operations.v2.transitions import finish_attempt
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.composition.models import (
    CURRENT_V2_POLICY,
    CompositionContext,
    V2CompositionPolicy,
)
from src.domains.agentverse.agents.genesis.models import (
    GenesisAuditError,
    GenesisAuditMissing,
    GenesisOperationInProgress,
    GenesisProvisionRequest,
    GenesisProvisionResult,
    GenesisStage,
)
from src.domains.agentverse.audit.models import CAOSLogRecord, LogEvent, stable_event_id
from src.domains.agentverse.audit.repository import LogConflict, LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_PROVISIONED_SIGNAL,
    STORE_CREATE_PROVISIONED_VERSION,
)
from src.domains.agentverse.tools.validation_tool.models import ValidationResult
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

if TYPE_CHECKING:
    from src.domains.agentverse.security.utu import UTUAuthority

OPERATION_KIND = "genesis.provision"
OPERATION_PRODUCER = "genesis"
# Composition is an in-process step under the same verified permit, not a CPBX grant.
COMPOSITION_SIGNAL, COMPOSITION_VERSION = "agent.composition", "1.0.0"
# Backend availability is transient; other composition rejections are definitive.
_TRANSIENT_COMPOSITION = frozenset(
    {
        "composition.backend_catalog_unavailable",
        "composition.backend_unavailable",
        "composition.backend_unregistered",
    }
)
_MAX_CODES = 64


def genesis_operation_id(permit: ExecutionPermit, request: GenesisProvisionRequest) -> UUID:
    """Same verified identity + same slot + same creation input = same operation."""
    key = permit.key
    try:
        digest = sha256_hex(
            {
                "kind": OPERATION_KIND,
                "tenant_id": key.tenant_id,
                "principal_id": key.agent_id,
                "subject_id": key.user_id,
                "slot": request.slot,
                "creation": request.creation,
            }
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Genesis creation input must be plain JSON") from exc
    return UUID(digest[:32])


class GenesisProvisioner:
    def __init__(
        self,
        *,
        operations: ManagedOperationRepository,
        log: AuditSink,
        composer: AgentComposer,
        authority: UTUAuthority,
        policy: V2CompositionPolicy = CURRENT_V2_POLICY,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_ms: int = 120_000,
    ) -> None:
        self._operations = operations
        self._log = log
        self._composer = composer
        self._authority = authority
        self._policy = policy
        self._clock = clock
        self._lease_ms = lease_ms
        self._owner_id = f"genesis:{uuid4()}"

    async def provision(
        self, client: AuthorizedSynapticClient, request: GenesisProvisionRequest
    ) -> GenesisProvisionResult:
        if not isinstance(client, AuthorizedSynapticClient):
            raise EventPermissionError("Genesis requires a CPBX-issued client")
        permit = client.execution_permit
        if not isinstance(permit, ExecutionPermit):
            raise EventPermissionError("Genesis requires a verified UTU execution permit")
        # Live authority before any operation state is created or read.
        await self._authority.check_permit(permit)
        request = GenesisProvisionRequest.model_validate(request)
        operation_id = genesis_operation_id(permit, request)

        record = await self._open(operation_id)
        state = record.snapshot.state
        if state.terminal:
            return await self._completed(record, permit)
        if state == OperationState.RUNNING and self._expired(record):
            record = await self._reconcile(record)
        elif state in {OperationState.RUNNING, OperationState.CANCEL_REQUESTED}:
            raise GenesisOperationInProgress(operation_id, interrupted=self._expired(record))
        record = await self._activate(record)
        return await _Attempt(self, record, client, permit, request).run()

    # ------------------------------------------------------------------
    # Operation lifecycle (OPS V2 managed aggregate)
    # ------------------------------------------------------------------

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

    async def _activate(self, record: ManagedOperation) -> ManagedOperation:
        operation_id = record.snapshot.operation_id
        try:
            return await self._operations.activate(
                operation_id,
                expected_revision=record.snapshot.revision,
                expected_generation=record.execution.generation,
                attempt_id=uuid4(),
                owner_id=self._owner_id,
                lease_ms=self._lease_ms,
                at=self._clock(),
                command_id=uuid4(),
            )
        except (RevisionConflict, ObsoleteFencingToken, OwnershipConflict, AttemptConflict):
            # A concurrent request activated first; it owns this execution.
            raise GenesisOperationInProgress(operation_id, interrupted=False) from None

    async def _reconcile(self, record: ManagedOperation) -> ManagedOperation:
        """Resolve an interrupted attempt to retry-pending; any race means someone else
        holds or resolved it, so report instead of acting."""
        operation_id = record.snapshot.operation_id
        attempt_id = record.snapshot.attempts[-1].attempt_id
        try:
            taken = await self._operations.takeover(
                operation_id,
                expected_revision=record.snapshot.revision,
                expected_generation=record.execution.generation,
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
                reason="interrupted attempt reconciled by genesis",
                at=self._clock(),
                command_id=uuid4(),
            )
        except (
            RevisionConflict,
            ObsoleteFencingToken,
            OwnershipConflict,
            AttemptConflict,
            LeaseExpired,
            OwnershipReleased,
        ):
            raise GenesisOperationInProgress(operation_id, interrupted=True) from None

    def _expired(self, record: ManagedOperation) -> bool:
        epoch = record.execution
        return epoch.status == EpochStatus.RELEASED or (
            epoch.lease_expires_at is not None and epoch.lease_expires_at <= self._clock()
        )

    async def _completed(
        self, record: ManagedOperation, permit: ExecutionPermit
    ) -> GenesisProvisionResult:
        """Answer a completed operation from Seshat's evidence; nothing re-executes."""
        operation = record.snapshot
        history = await self._log.history(permit.key.tenant_id, str(operation.operation_id))
        ids = tuple(r.event_id for r in history)
        base = {
            "operation_id": operation.operation_id,
            "correlation_id": str(operation.operation_id),
            "replayed": True,
            "audit_event_ids": ids,
        }
        if operation.state == OperationState.SUCCEEDED:
            stored = next((r for r in history if r.event_type == "genesis.store.completed"), None)
            if stored is None:
                raise GenesisAuditMissing(
                    f"Succeeded Genesis operation {operation.operation_id} has no store evidence"
                )
            return GenesisProvisionResult(
                **base,
                status="provisioned",
                stage="store",
                agent_id=stored.subject.resource_id,
                created=stored.metadata["created"],
            )
        last = next((r for r in reversed(history) if r.outcome != "succeeded"), None)
        if last is None:
            return GenesisProvisionResult(**base, status="failed")
        return GenesisProvisionResult(
            **base,
            status="rejected" if last.outcome == "rejected" else "failed",
            stage=_STAGES[last.event_type],
            issue_codes=_codes_of(last),
        )


_STAGES: dict[str, GenesisStage] = {
    "genesis.validation.completed": "validation",
    "genesis.composition.completed": "composition",
    "genesis.store.completed": "store",
    "genesis.store.failed": "store",
}


def _codes_of(record: CAOSLogRecord) -> tuple[str, ...]:
    metadata = record.metadata
    if "issue_codes" in metadata:
        return tuple(metadata["issue_codes"])
    return (metadata["code"],) if "code" in metadata else ()


def _issue_codes(issues: Any) -> list[str]:
    return sorted({issue.code for issue in issues})[:_MAX_CODES]


class _Attempt:
    """One execution attempt holding the managed lease. Not reusable."""

    def __init__(
        self,
        owner: GenesisProvisioner,
        record: ManagedOperation,
        client: AuthorizedSynapticClient,
        permit: ExecutionPermit,
        request: GenesisProvisionRequest,
    ) -> None:
        self._owner = owner
        self._record = record
        self._client = client
        self._permit = permit
        self._request = request
        self._operation_id = record.snapshot.operation_id
        self._correlation_id = str(self._operation_id)
        self._attempt = record.snapshot.attempts[-1]
        self._events: list[str] = []
        self._finishing = False

    async def run(self) -> GenesisProvisionResult:
        try:
            return await self._execute()
        except LogError as exc:
            if self._finishing:
                raise
            # Never succeed without evidence. Conflicting/corrupt evidence is definitive.
            await self._finish(AttemptState.FAILED, reason=f"audit:{exc.code}", retry=exc.retryable)
            raise GenesisAuditError(self._operation_id, exc, retryable=exc.retryable) from exc
        except BaseException as exc:
            if self._finishing:
                # The outcome commit itself failed (possibly applied): never commit a
                # second outcome over it. The operation stays for inspection.
                raise
            # Authorization denials, unexpected errors and cancellation: every effect is
            # idempotent, so leave the operation retry-pending rather than claim an outcome.
            await self._finish(
                AttemptState.FAILED, reason=f"error:{type(exc).__name__}", retry=True
            )
            raise

    async def _execute(self) -> GenesisProvisionResult:
        slot = self._request.slot

        # 1-3. Validation through CPBX, then its confirmed outcome.
        envelope = await self._client.request(
            DATA_VALIDATION_SIGNAL,
            {"resource_type": "agent", "payload": self._request.creation},
            correlation_id=self._correlation_id,
            causation_id=self._correlation_id,
        )
        if not (isinstance(envelope, dict) and envelope.get("success") is True):
            raise TypeError("data.validation returned no result")
        validation = ValidationResult.model_validate(envelope["result"])
        codes = _issue_codes(validation.issues)
        validated = await self._audit(
            "genesis.validation.completed",
            outcome="succeeded" if validation.valid else "rejected",
            target=(DATA_VALIDATION_SIGNAL, DATA_VALIDATION_VERSION),
            causation_id=self._correlation_id,
            metadata={
                "resource_type": "agent",
                "valid": validation.valid,
                "issue_codes": codes,
                "normalized_digest": validation.normalized_digest,
            },
        )
        if not validation.valid:
            return await self._stop("validation", "rejected", codes, retry=False)

        # 4-5. Composition of the trusted, requester-bound validation result.
        composition = self._owner._composer.compose(
            validation,
            context=CompositionContext.from_permit(self._permit),
            policy=self._owner._policy,
        )
        codes = _issue_codes(composition.issues)
        composed = await self._audit(
            "genesis.composition.completed",
            outcome="succeeded" if composition.composed else "rejected",
            target=(COMPOSITION_SIGNAL, COMPOSITION_VERSION),
            causation_id=validated.event_id,
            metadata={
                "composed": composition.composed,
                "issue_codes": codes,
                "validation_digest": validation.normalized_digest,
            },
        )
        if not composition.composed:
            transient = bool(codes) and set(codes) <= _TRANSIENT_COMPOSITION
            return await self._stop(
                "composition", "failed" if transient else "rejected", codes, retry=transient
            )

        # 6-7. Policy-pinned Store write through CPBX, then its confirmed outcome.
        envelope = await self._client.request(
            STORE_CREATE_PROVISIONED_SIGNAL,
            {"resource_type": "agent", "slot": slot, "payload": composition.candidate},
            correlation_id=self._correlation_id,
            causation_id=composed.event_id,
        )
        if not isinstance(envelope, dict) or not isinstance(envelope.get("success"), bool):
            raise TypeError("store.create.provisioned returned no result")
        if not envelope["success"]:
            meta = envelope.get("meta") or {}
            code, retryable = str(meta.get("code") or "STORE_ERROR"), meta.get("retryable") is True
            await self._audit(
                "genesis.store.failed",
                outcome="failed" if retryable else "rejected",
                target=(STORE_CREATE_PROVISIONED_SIGNAL, STORE_CREATE_PROVISIONED_VERSION),
                causation_id=composed.event_id,
                metadata={"resource_type": "agent", "code": code, "retryable": retryable},
            )
            return await self._stop(
                "store", "failed" if retryable else "rejected", [code], retry=retryable
            )
        stored = envelope["result"]
        evidence = await self._audit(
            "genesis.store.completed",
            outcome="succeeded",
            target=(STORE_CREATE_PROVISIONED_SIGNAL, STORE_CREATE_PROVISIONED_VERSION),
            causation_id=composed.event_id,
            resource_id=stored["resource_id"],
            metadata={
                "resource_type": "agent",
                "created": stored["created"],
                "request_digest": stored["request_digest"],
                "slot": slot,
            },
        )
        # 8. Succeed only after the store evidence is durable.
        await self._finish(AttemptState.SUCCEEDED)
        return GenesisProvisionResult(
            operation_id=self._operation_id,
            correlation_id=self._correlation_id,
            status="provisioned",
            stage="store",
            agent_id=evidence.subject.resource_id,
            created=evidence.metadata["created"],
            audit_event_ids=tuple(self._events),
        )

    async def _audit(
        self,
        event_type: str,
        *,
        outcome: str,
        target: tuple[str, str],
        causation_id: str,
        metadata: dict[str, Any],
        resource_id: str | None = None,
    ) -> CAOSLogRecord:
        # Successes are one fact per operation; rejections/failures one per attempt.
        step = self._request.slot
        if outcome != "succeeded":
            step = f"{step}:attempt-{self._attempt.attempt_number}"
        event = LogEvent(
            event_id=stable_event_id(
                correlation_id=self._correlation_id, event_type=event_type, step=step
            ),
            event_type=event_type,
            outcome=outcome,
            subject={"resource_type": "agent", "resource_id": resource_id},
            target={"signal": target[0], "version": target[1]},
            correlation={
                "correlation_id": self._correlation_id,
                "causation_id": causation_id,
                "operation_id": self._operation_id,
            },
            metadata=metadata,
        )
        authorization = permit_provenance(self._permit, target[0])
        log = self._owner._log
        try:
            result = await log.append(event, authorization=authorization)
            record = result.record
        except LogConflict:
            # Preserve earlier successes: Store may report created=False on replay,
            # and a fresh verified session has a different authorization evidence digest.
            record = await log.get(self._permit.key.tenant_id, event.event_id)
            if not (
                _same_store_fact(record, event, authorization)
                or _same_success_after_reauthentication(record, event, authorization)
            ):
                raise
        self._events.append(record.event_id)
        return record

    async def _stop(
        self, stage: GenesisStage, status: str, codes: list[str], *, retry: bool
    ) -> GenesisProvisionResult:
        await self._finish(AttemptState.FAILED, reason=f"{stage}:{status}", retry=retry)
        return GenesisProvisionResult(
            operation_id=self._operation_id,
            correlation_id=self._correlation_id,
            status=status,  # type: ignore[arg-type]
            stage=stage,
            retryable=retry,
            issue_codes=tuple(codes),
            audit_event_ids=tuple(self._events),
        )

    async def _finish(
        self, outcome: AttemptState, *, reason: str | None = None, retry: bool = False
    ) -> None:
        self._finishing = True
        snapshot = finish_attempt(
            self._record.snapshot,
            attempt_id=self._attempt.attempt_id,
            outcome=outcome,
            at=self._owner._clock(),
            reason=reason,
            retry=retry,
        )
        self._record = await self._owner._operations.commit_outcome(
            snapshot,
            expected_revision=self._record.snapshot.revision,
            attempt_id=self._attempt.attempt_id,
            owner_id=self._owner._owner_id,
            generation=self._record.execution.generation,
            command_id=uuid4(),
        )


def _same_success_after_reauthentication(
    record: CAOSLogRecord | None, event: LogEvent, authorization: Any
) -> bool:
    """Keep an earlier observed success, never relabel it with a new session.

    G1.4 may recover under a newly verified operator session after logout/expiry.
    A session-bound evidence digest changes, but an immutable logical success
    belongs to the operation, not to its retry. Identity and policy must still
    match and every fact must be identical (Store may report an idempotent replay).
    The caller has independently re-executed the governed step under live authority.
    """
    if record is None or record.outcome != "succeeded" or event.outcome != "succeeded":
        return False
    # The session-bound evidence digest and origin (operator session or bootstrap
    # lease) change with the authority that retried; identity and policy may not.
    reauthenticated = {"evidence_digest", "origin"}
    if record.authorization.model_dump(exclude=reauthenticated) != authorization.model_dump(
        exclude=reauthenticated
    ):
        return False
    return (
        record.event_id == event.event_id
        and record.event_type == event.event_type
        and record.subject == event.subject
        and record.target == event.target
        and record.correlation_id == event.correlation.correlation_id
        and record.causation_id == event.correlation.causation_id
        and record.operation_id == event.correlation.operation_id
        and (
            record.metadata == event.metadata
            or (
                event.event_type == "genesis.store.completed"
                and record.metadata["created"] is True
                and event.metadata["created"] is False
                and _same_store_fact(record, event, record.authorization)
            )
        )
    )


def _same_store_fact(record: CAOSLogRecord | None, event: LogEvent, authorization: Any) -> bool:
    if record is None or event.event_type != "genesis.store.completed":
        return False
    keep = ("resource_type", "request_digest", "slot")
    return (
        record.event_type == event.event_type
        and record.outcome == event.outcome
        and record.subject == event.subject
        and record.target == event.target
        and record.correlation_id == event.correlation.correlation_id
        and record.causation_id == event.correlation.causation_id
        and record.authorization == authorization
        and all(record.metadata[k] == event.metadata[k] for k in keep)
    )
