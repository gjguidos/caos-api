"""Dispatch hints over the existing signed, policy-enforced Event V2 runtime.

A notice is an at-least-once hint carrying references only. It is not operation truth,
not a claim and not permission: consumers must inspect and activate through the managed
repository's fenced commands. Duplicate or missing notices are expected; discovery
recovers missed ones. No notification state is persisted.
"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.base.events.v2.policy import EventPrincipal
from src.base.events.v2.registry import CognitiveEventRegistry, EventContract
from src.base.events.v2.runtime import CognitiveEventRuntime
from src.base.operations.v2.admission import Identifier, Signal
from src.base.operations.v2.managed import ManagedOperation

DISPATCH_EVENT_TYPE = "operations.dispatch.available"


class OperationDispatchNotice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    notice_version: Literal[1] = 1
    operation_id: UUID
    tenant_id: Identifier
    signal: Signal
    capability_version: Identifier
    correlation_id: Identifier
    causation_id: Identifier | None

    @classmethod
    def for_operation(cls, record: ManagedOperation) -> OperationDispatchNotice:
        if record.admission is None:
            raise ValueError("Only admitted operations have dispatch notices")
        descriptor = record.admission.descriptor
        return cls(
            operation_id=record.snapshot.operation_id,
            tenant_id=descriptor.tenant_id,
            signal=descriptor.capability.signal,
            capability_version=descriptor.capability.version,
            correlation_id=descriptor.correlation_id,
            causation_id=descriptor.causation_id,
        )


class DispatchNotifier(Protocol):
    async def notify(self, notice: OperationDispatchNotice) -> None: ...


def register_dispatch_contract(registry: CognitiveEventRegistry) -> None:
    """Trusted bootstrap only. Registration is a contract, not a publish grant."""
    registry.register_contract(EventContract(DISPATCH_EVENT_TYPE, OperationDispatchNotice, 1))


class CognitiveEventDispatchNotifier:
    """Publishes as a configured system principal; the Event V2 policy must grant it.

    The envelope tenant is the operation's tenant. RabbitMQ publisher confirms and
    mandatory routing come from the existing client; failures propagate to the caller.
    """

    def __init__(self, runtime: CognitiveEventRuntime, *, principal_id: str) -> None:
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("Dispatch notices require a trusted system principal")
        self._runtime = runtime
        self._principal_id = principal_id

    async def notify(self, notice: OperationDispatchNotice) -> None:
        client = self._runtime.bind(EventPrincipal(self._principal_id, notice.tenant_id))
        await client.emit(
            DISPATCH_EVENT_TYPE,
            notice.model_dump(mode="json"),
            correlation_id=notice.correlation_id,
            causation_id=str(notice.operation_id),
        )
