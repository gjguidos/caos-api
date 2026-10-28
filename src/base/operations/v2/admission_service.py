"""Durable admission: resolve exact capability, persist descriptor, hint dispatch.

Trusted callers supply ``AuthorizationProvenance`` produced by a UTU adapter after it
verified the requester's permit and invoke grant. This service records that evidence;
it is not an authorization decision and does not mint authority for later execution.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from src.base.operations.v2.admission import (
    ADMISSION_PRODUCER,
    APPROVED_RECOVERY_POLICIES,
    MANUAL_REVIEW_V1,
    AdmissionRecord,
    AuthorizationProvenance,
    CapabilityCatalog,
    ExecutionDescriptor,
    Identifier,
    InlineInput,
    InputReference,
    InputReferenceVerifier,
    InvalidAdmission,
    OperationAdmissionRepository,
    RecoveryPolicyRef,
    Signal,
    UnapprovedRecoveryPolicy,
    UnknownCapability,
    discovery_key,
    idempotency_scope,
)
from src.base.operations.v2.dispatch import DispatchNotifier, OperationDispatchNotice
from src.base.operations.v2.managed import ManagedOperation
from src.base.operations.v2.models import OperationRecord, OperationState

logger = logging.getLogger(__name__)


class AdmissionRequest(BaseModel):
    """Caller-requested work. Identity, tenant and authority are never read from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: Signal
    version: Identifier
    input: dict[str, Any] | InputReference
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    recovery_policy: RecoveryPolicyRef = MANUAL_REVIEW_V1
    correlation_id: Identifier
    causation_id: Identifier | None = None


@dataclass(frozen=True)
class AdmissionResult:
    operation: ManagedOperation
    created: bool
    notified: bool  # A hint only; False never means the work is lost.


class OperationAdmissionService:
    def __init__(
        self,
        *,
        repository: OperationAdmissionRepository,
        catalog: CapabilityCatalog,
        notifier: DispatchNotifier | None = None,
        references: InputReferenceVerifier | None = None,
        approved_recovery_policies: frozenset[RecoveryPolicyRef] = APPROVED_RECOVERY_POLICIES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._notifier = notifier
        self._references = references
        self._policies = frozenset(approved_recovery_policies)
        self._clock = clock

    async def admit(
        self, request: AdmissionRequest, *, authorization: AuthorizationProvenance
    ) -> AdmissionResult:
        request = AdmissionRequest.model_validate(request)
        authorization = AuthorizationProvenance.model_validate(authorization, strict=True)
        if request.recovery_policy not in self._policies:
            raise UnapprovedRecoveryPolicy(request.recovery_policy)
        try:
            capability = self._catalog.resolve(request.signal, request.version)
        except (LookupError, ValueError) as exc:
            raise UnknownCapability(f"{request.signal}@{request.version}") from exc
        if (capability.signal, capability.version) != (request.signal, request.version):
            # Never silently substitute another (e.g. latest) provider version.
            raise UnknownCapability(f"{request.signal}@{request.version}")
        source: InlineInput | InputReference
        if isinstance(request.input, InputReference):
            # Fail closed: a well-formed URI and digest prove neither integrity nor retention.
            if self._references is None:
                raise InvalidAdmission("No approved immutable input store is configured")
            await self._references.verify(request.input, tenant_id=authorization.tenant_id)
            source = request.input
        else:
            source = InlineInput.from_value(request.input)
        try:
            descriptor = ExecutionDescriptor(
                descriptor_version=1,
                capability=capability,
                input=source,
                tenant_id=authorization.tenant_id,
                requester_id=authorization.principal_id,
                authorization=authorization,
                recovery_policy=request.recovery_policy,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
            )
        except ValueError as exc:
            raise InvalidAdmission("Invalid execution descriptor") from exc
        operation = OperationRecord.create(
            kind=capability.signal,
            producer=ADMISSION_PRODUCER,
            at=self._clock(),
            correlation_id=descriptor.correlation_id,
            causation_id=descriptor.causation_id,
        )
        admission = AdmissionRecord(
            descriptor=descriptor,
            idempotency_scope=idempotency_scope(
                descriptor.tenant_id, descriptor.requester_id, request.idempotency_key
            ),
            discovery_key=discovery_key(operation),
        )
        record, created = await self._repository.admit(operation, admission)
        return AdmissionResult(record, created, await self._notify(record))

    async def _notify(self, record: ManagedOperation) -> bool:
        if self._notifier is None or record.snapshot.state != OperationState.PENDING:
            return False
        try:
            await self._notifier.notify(OperationDispatchNotice.for_operation(record))
        except Exception:
            # Durable admission already succeeded; discovery recovers the missed hint.
            logger.warning(
                "Dispatch notice not confirmed for operation %s",
                record.snapshot.operation_id,
                exc_info=True,
            )
            return False
        return True
