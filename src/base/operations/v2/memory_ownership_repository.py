"""Single-event-loop reference adapter, with an injectable repository clock."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter

from src.base.operations.v2.models import Timestamp
from src.base.operations.v2.ownership import (
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipRecord,
    OwnershipState,
    new_claim,
    require_live,
    validate_claim,
    validate_duration,
    validate_identity,
    validate_key,
)


class InMemoryOwnershipRepository:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[UUID, OwnershipRecord] = {}
        self._lock = asyncio.Lock()

    def _now(self) -> datetime:
        now = TypeAdapter(Timestamp).validate_python(self._clock())
        return now.replace(microsecond=now.microsecond // 1000 * 1000)

    def _get(self, operation_id: UUID) -> OwnershipRecord:
        if operation_id not in self._records:
            raise OwnershipNotFound(operation_id)
        return OwnershipRecord.model_validate(self._records[operation_id], strict=True)

    async def inspect(self, operation_id: UUID) -> OwnershipRecord:
        validate_identity(operation_id)
        async with self._lock:
            return self._get(operation_id)

    async def claim(
        self, operation_id: UUID, *, attempt_id: UUID, owner_id: str, lease_ms: int
    ) -> OwnershipRecord:
        validate_claim(operation_id, attempt_id, owner_id, lease_ms)
        async with self._lock:
            now = self._now()
            previous = self._get(operation_id) if operation_id in self._records else None
            if previous is not None and (
                now < previous.updated_at
                or (previous.state == OwnershipState.ACTIVE and previous.lease_expires_at > now)
            ):
                raise OwnershipConflict(operation_id)
            record = new_claim(
                operation_id,
                attempt_id=attempt_id,
                owner_id=owner_id,
                lease_ms=lease_ms,
                now=now,
                previous=previous,
            )
            self._records[operation_id] = record
            return OwnershipRecord.model_validate(record, strict=True)

    async def renew(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int, lease_ms: int
    ) -> OwnershipRecord:
        validate_key(operation_id, owner_id, fencing_token)
        validate_duration(lease_ms)
        async with self._lock:
            record, now = self._get(operation_id), self._now()
            require_live(record, owner_id=owner_id, fencing_token=fencing_token, now=now)
            following = OwnershipRecord.model_validate(
                {
                    **record.model_dump(),
                    "updated_at": now,
                    "lease_expires_at": max(
                        record.lease_expires_at, now + timedelta(milliseconds=lease_ms)
                    ),
                }
            )
            self._records[operation_id] = following
            return OwnershipRecord.model_validate(following, strict=True)

    async def release(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int
    ) -> OwnershipRecord:
        validate_key(operation_id, owner_id, fencing_token)
        async with self._lock:
            record, now = self._get(operation_id), self._now()
            require_live(record, owner_id=owner_id, fencing_token=fencing_token, now=now)
            following = OwnershipRecord.model_validate(
                {
                    **record.model_dump(),
                    "state": OwnershipState.RELEASED,
                    "updated_at": now,
                    "released_at": now,
                }
            )
            self._records[operation_id] = following
            return OwnershipRecord.model_validate(following, strict=True)
