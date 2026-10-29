"""Managed aggregate contracts: storage authority, never execution or permission."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from src.base.operations.v2.admission import ADMISSION_PRODUCER, AdmissionRecord, discovery_key
from src.base.operations.v2.models import (
    AttemptState,
    NonBlankText,
    OperationRecord,
    OperationState,
    Timestamp,
)
from src.base.operations.v2.ownership import MAX_FENCING_TOKEN, InvalidOwnershipRequest


class InvalidManagedRequest(InvalidOwnershipRequest):
    """Invalid expected revision/generation, identity or unsupported command."""


class CorruptManagedOperation(ValueError):
    """The managed storage envelope cannot be restored without repair."""


class AttemptConflict(ValueError):
    """The supplied attempt is not the authoritative current attempt."""


class ReconciliationOnly(ValueError):
    """Reconciliation ownership does not authorize execution or outcome reports."""


class ReconciliationRequired(ValueError):
    """Resolving an interrupted attempt requires reconciliation (takeover) ownership."""


class AmbiguousManagedWrite(RuntimeError):
    """A database failure leaves acceptance uncertain; inspect, never blindly replay."""

    def __init__(self, operation_id: UUID, command_id: UUID | None) -> None:
        self.operation_id = operation_id
        self.command_id = command_id
        super().__init__(f"Inspect managed operation {operation_id}; write outcome is uncertain")


class EpochStatus(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    RELEASED = "released"
    CLOSED = "closed"


class EpochMode(StrEnum):
    EXECUTE = "execute"
    RECONCILE = "reconcile"


class ExecutionEpoch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    generation: Annotated[int, Field(strict=True, ge=0, le=MAX_FENCING_TOKEN)]
    owner_id: NonBlankText | None
    attempt_id: UUID | None
    status: EpochStatus
    mode: EpochMode | None
    acquired_at: Timestamp | None
    updated_at: Timestamp | None
    lease_expires_at: Timestamp | None
    released_at: Timestamp | None

    @model_validator(mode="after")
    def consistent_epoch(self) -> Self:
        values = (
            self.owner_id,
            self.attempt_id,
            self.mode,
            self.acquired_at,
            self.updated_at,
            self.lease_expires_at,
        )
        if self.generation == 0:
            if self.status != EpochStatus.IDLE or any(value is not None for value in values):
                raise ValueError("Generation zero is never-acquired idle ownership")
            if self.released_at is not None:
                raise ValueError("Idle ownership cannot have a release timestamp")
        else:
            if self.status == EpochStatus.IDLE or any(value is None for value in values):
                raise ValueError("Acquired ownership requires identity, mode and lease timestamps")
            assert self.acquired_at is not None and self.updated_at is not None
            assert self.lease_expires_at is not None
            if not self.acquired_at <= self.updated_at < self.lease_expires_at:
                raise ValueError("Inconsistent execution lease timestamps")
            retired = self.status in {EpochStatus.RELEASED, EpochStatus.CLOSED}
            if retired != (self.released_at is not None):
                raise ValueError("Retired ownership requires released_at")
            if self.released_at is not None and self.released_at != self.updated_at:
                raise ValueError("Release/update timestamps must agree")
        times = (self.acquired_at, self.updated_at, self.lease_expires_at, self.released_at)
        if any(at is not None and at.microsecond % 1000 for at in times):
            raise ValueError("Execution clocks require BSON millisecond precision")
        return self

    @classmethod
    def idle(cls) -> Self:
        return cls(
            generation=0,
            owner_id=None,
            attempt_id=None,
            status=EpochStatus.IDLE,
            mode=None,
            acquired_at=None,
            updated_at=None,
            lease_expires_at=None,
            released_at=None,
        )


class StateChangeReceipt(BaseModel):
    """One retained receipt, not an unlimited idempotency ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    command_id: UUID
    action: Literal["activate", "outcome"]
    request_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    generation: Annotated[int, Field(strict=True, ge=1, le=MAX_FENCING_TOKEN)]
    attempt_id: UUID
    previous_revision: Annotated[int, Field(strict=True, ge=0)]
    resulting_revision: Annotated[int, Field(strict=True, ge=1)]


class ManagedOperation(BaseModel):
    """Schema 2: OPS2-04B aggregate. Schema 3: the same aggregate plus admission.

    Versions are explicit; a schema-2 record is never upgraded in place.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[2, 3]
    snapshot: OperationRecord
    execution: ExecutionEpoch
    last_state_change: StateChangeReceipt | None
    admission: AdmissionRecord | None = None

    @model_serializer(mode="wrap")
    def _schema_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.admission is None:
            data.pop("admission", None)  # Schema 2 serializes exactly as in OPS2-04B.
        return data

    @model_validator(mode="after")
    def consistent_admission(self) -> Self:
        if (self.schema_version == 3) != (self.admission is not None):
            raise ValueError("Only schema-3 aggregates carry, and require, admission")
        if self.admission is not None:
            operation, descriptor = self.snapshot, self.admission.descriptor
            if (
                operation.kind != descriptor.capability.signal
                or operation.producer != ADMISSION_PRODUCER
                or operation.correlation_id != descriptor.correlation_id
                or operation.causation_id != descriptor.causation_id
            ):
                raise ValueError("Admitted snapshot metadata must match its descriptor")
            if self.admission.discovery_key != discovery_key(operation):
                raise ValueError("Discovery key must derive from operation identity and creation")
        return self

    @model_validator(mode="after")
    def consistent_aggregate(self) -> Self:
        operation, epoch = self.snapshot, self.execution
        if epoch.generation == 0:
            if operation.state != OperationState.PENDING or self.last_state_change is not None:
                raise ValueError("Only a new pending operation can have an idle epoch")
            return self
        if not operation.attempts or epoch.attempt_id != operation.attempts[-1].attempt_id:
            raise ValueError("Epoch must bind the latest operation attempt")
        if epoch.attempt_id == operation.operation_id:
            raise ValueError("Operation and attempt identities must differ")
        if operation.state in {OperationState.RUNNING, OperationState.CANCEL_REQUESTED}:
            if epoch.status not in {EpochStatus.ACTIVE, EpochStatus.RELEASED}:
                raise ValueError("Unresolved work requires active or released ownership")
        elif operation.state == OperationState.RETRY_PENDING:
            if epoch.status != EpochStatus.RELEASED:
                raise ValueError("Completed retry intent requires released ownership")
        elif operation.state.terminal:
            if epoch.status != EpochStatus.CLOSED:
                raise ValueError("Terminal operations require closed execution authority")
        else:
            raise ValueError("Pending operations cannot have acquired execution authority")
        receipt = self.last_state_change
        if receipt is None or not (
            receipt.resulting_revision == operation.revision == receipt.previous_revision + 1
            and receipt.generation <= epoch.generation
            and receipt.attempt_id == epoch.attempt_id
        ):
            raise ValueError("State receipt must bind the latest snapshot revision and attempt")
        if (receipt.action == "activate") != (operation.attempts[-1].state == AttemptState.RUNNING):
            raise ValueError("Receipt action contradicts the latest attempt outcome")
        return self


class ManagedOperationRepository(Protocol):
    """No migrations, generic unfenced updates, execution or recovery commands."""

    async def create(self, operation: OperationRecord) -> ManagedOperation: ...

    async def inspect(self, operation_id: UUID) -> ManagedOperation: ...

    async def activate(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        expected_generation: int,
        attempt_id: UUID,
        owner_id: str,
        lease_ms: int,
        at: datetime,
        command_id: UUID,
    ) -> ManagedOperation: ...

    async def renew(
        self,
        operation_id: UUID,
        *,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        lease_ms: int,
    ) -> ManagedOperation: ...

    async def commit_outcome(
        self,
        operation: OperationRecord,
        *,
        expected_revision: int,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        command_id: UUID,
    ) -> ManagedOperation: ...

    async def release(
        self,
        operation_id: UUID,
        *,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
    ) -> ManagedOperation: ...

    async def takeover(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        expected_generation: int,
        attempt_id: UUID,
        owner_id: str,
        lease_ms: int,
    ) -> ManagedOperation: ...

    async def resolve_reconciled(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        reason: str,
        at: datetime,
        command_id: UUID,
    ) -> ManagedOperation: ...
