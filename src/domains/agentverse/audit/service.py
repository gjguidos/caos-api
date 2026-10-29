"""Trusted, append-only CAOS audit logging.

In-process only: it is not a synaptic capability, a V1 tool or an HTTP route, so
agents have no handle to it. Callers are trusted runtime code that already holds a
verified UTU permit; attribution comes from that permit's provenance, never from the
event. Persistence failures propagate (typed, with ``retryable``): whether an audit
failure aborts the audited operation is the caller's decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.base.operations.v2.admission import AuthorizationProvenance
from src.domains.agentverse.audit.models import CAOSLogRecord, LogEvent
from src.domains.agentverse.audit.repository import MongoLogRepository


@dataclass(frozen=True)
class LogAppendResult:
    record: CAOSLogRecord
    created: bool  # False: an identical event was already recorded.


class AuditSink(Protocol):
    """Seshat's producer contract. Producers depend on this, never on storage.

    ``append`` returns only after the record is durably accepted (or raises a typed,
    retryable ``LogError``). A future distributed ingestion path must preserve that
    acknowledgement for mandatory audit; it may not weaken it to fire-and-forget.
    """

    async def append(
        self, event: LogEvent, *, authorization: AuthorizationProvenance
    ) -> LogAppendResult: ...

    async def get(self, tenant_id: str, event_id: str) -> CAOSLogRecord | None: ...

    async def history(self, tenant_id: str, correlation_id: str) -> list[CAOSLogRecord]: ...


class CAOSLogService:
    def __init__(
        self,
        *,
        repository: MongoLogRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def append(
        self, event: LogEvent, *, authorization: AuthorizationProvenance
    ) -> LogAppendResult:
        if not isinstance(event, LogEvent):
            raise TypeError("A validated LogEvent is required")
        if not isinstance(authorization, AuthorizationProvenance):
            # Use permit_provenance(permit, signal) on a verified UTU permit.
            raise TypeError("Audit attribution requires verified authorization provenance")
        record = CAOSLogRecord.build(event, authorization=authorization, recorded_at=self._clock())
        stored, created = await self._repository.append(record)
        return LogAppendResult(record=stored, created=created)

    # Read-only, tenant-scoped evidence for trusted coordinators (no client/agent API).

    async def get(self, tenant_id: str, event_id: str) -> CAOSLogRecord | None:
        return await self._repository.get(tenant_id, event_id)

    async def history(self, tenant_id: str, correlation_id: str) -> list[CAOSLogRecord]:
        return await self._repository.list_by_correlation(tenant_id, correlation_id)
