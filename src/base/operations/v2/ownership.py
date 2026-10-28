"""Execution coordination contracts. Ownership is never a UTU permission grant."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.base.operations.v2.models import NonBlankText, Timestamp

MAX_FENCING_TOKEN = 2**63 - 1
MAX_LEASE_MS = 86_400_000


class OwnershipConflict(ValueError):
    """Another owner holds the lease, or a concurrent change won."""


class ObsoleteFencingToken(ValueError):
    """The supplied token is not the current generation."""


class LeaseExpired(ValueError):
    """The current generation's lease has expired."""


class OwnershipNotFound(LookupError):
    """There has been no claim for this operation."""


class OwnershipReleased(ValueError):
    """This generation was already released and cannot be revived."""


class InvalidOwnershipRequest(ValueError):
    """Malformed identity, token, duration or exhausted token space."""


class CorruptOwnership(ValueError):
    """Stored ownership cannot be restored without repair."""


class OwnershipState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


class OwnershipRecord(BaseModel):
    """Latest generation, retained even after expiration/release.

    ACTIVE describes an unreleased generation, not proof it is still unexpired.
    Every field is required on restoration. BSON millisecond precision is explicit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    operation_id: UUID
    attempt_id: UUID
    owner_id: NonBlankText
    fencing_token: Annotated[int, Field(strict=True, ge=1, le=MAX_FENCING_TOKEN)]
    state: OwnershipState
    created_at: Timestamp
    acquired_at: Timestamp
    updated_at: Timestamp
    lease_expires_at: Timestamp
    released_at: Timestamp | None

    @model_validator(mode="after")
    def consistent_ownership(self) -> Self:
        if self.operation_id == self.attempt_id:
            raise ValueError("Operation and attempt identities must differ")
        if not self.created_at <= self.acquired_at <= self.updated_at < self.lease_expires_at:
            raise ValueError("Ownership timestamps are inconsistent")
        if (self.state == OwnershipState.RELEASED) != (self.released_at is not None):
            raise ValueError("Only released ownership has released_at")
        if self.released_at is not None and self.released_at != self.updated_at:
            raise ValueError("Release and update timestamps must agree")
        times = (
            self.created_at,
            self.acquired_at,
            self.updated_at,
            self.lease_expires_at,
            self.released_at,
        )
        if any(at is not None and at.microsecond % 1000 for at in times):
            raise ValueError("Ownership timestamps require millisecond precision")
        return self


class OwnershipRepository(Protocol):
    """Atomic coordination only, with repository-authoritative time.

    No caller timestamp, deletion, execution, recovery or operation-state writes.
    A claim always creates a new generation; even the same owner cannot reclaim
    an unexpired lease. Renewal never shortens expiry. Release is strict, not
    idempotent. Both require a live, unreleased matching owner/token.
    """

    async def claim(
        self, operation_id: UUID, *, attempt_id: UUID, owner_id: str, lease_ms: int
    ) -> OwnershipRecord: ...

    async def renew(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int, lease_ms: int
    ) -> OwnershipRecord: ...

    async def release(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int
    ) -> OwnershipRecord: ...

    async def inspect(self, operation_id: UUID) -> OwnershipRecord: ...


def validate_identity(operation_id: UUID, owner_id: str | None = None) -> None:
    if not isinstance(operation_id, UUID):
        raise InvalidOwnershipRequest("Operation identity must be a UUID")
    if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):
        raise InvalidOwnershipRequest("Owner identity must be nonblank text")


def validate_claim(operation_id: UUID, attempt_id: UUID, owner_id: str, lease_ms: int) -> None:
    validate_identity(operation_id, owner_id)
    if owner_id is None or not isinstance(attempt_id, UUID) or operation_id == attempt_id:
        raise InvalidOwnershipRequest("Distinct attempt UUID and owner are required")
    validate_duration(lease_ms)


def validate_duration(lease_ms: int) -> None:
    if type(lease_ms) is not int or not 1 <= lease_ms <= MAX_LEASE_MS:
        raise InvalidOwnershipRequest(f"Lease duration must be 1..{MAX_LEASE_MS} milliseconds")


def validate_key(operation_id: UUID, owner_id: str, fencing_token: int) -> None:
    validate_identity(operation_id, owner_id)
    if (
        owner_id is None
        or type(fencing_token) is not int
        or not 1 <= fencing_token <= MAX_FENCING_TOKEN
    ):
        raise InvalidOwnershipRequest(
            "A current positive integer fencing token and owner are required"
        )


def require_live(
    record: OwnershipRecord, *, owner_id: str, fencing_token: int, now: datetime
) -> None:
    # Stable error precedence: token, owner, release, expiration, clock regression.
    if record.fencing_token != fencing_token:
        raise ObsoleteFencingToken(record.operation_id)
    if record.owner_id != owner_id:
        raise OwnershipConflict(record.operation_id)
    if record.state == OwnershipState.RELEASED:
        raise OwnershipReleased(record.operation_id)
    if record.lease_expires_at <= now:
        raise LeaseExpired(record.operation_id)
    if now < record.updated_at:
        raise OwnershipConflict("Repository clock moved backwards")


def new_claim(
    operation_id: UUID,
    *,
    attempt_id: UUID,
    owner_id: str,
    lease_ms: int,
    now: datetime,
    previous: OwnershipRecord | None = None,
) -> OwnershipRecord:
    token = 1 if previous is None else previous.fencing_token + 1
    if token > MAX_FENCING_TOKEN:
        raise InvalidOwnershipRequest("Fencing token space exhausted; never reset the record")
    return OwnershipRecord(
        operation_id=operation_id,
        attempt_id=attempt_id,
        owner_id=owner_id,
        fencing_token=token,
        state=OwnershipState.ACTIVE,
        created_at=now if previous is None else previous.created_at,
        acquired_at=now,
        updated_at=now,
        lease_expires_at=now + timedelta(milliseconds=lease_ms),
        released_at=None,
    )
