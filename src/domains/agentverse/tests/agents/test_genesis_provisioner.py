"""Genesis provisioning: real CPBX/UTU, data.validation, composer, provisioned Store,
Seshat and OPS V2 managed operation semantics."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from dependency_injector import providers

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.operations.v2.managed import (
    EpochMode,
    EpochStatus,
    ExecutionEpoch,
    ManagedOperation,
    StateChangeReceipt,
)
from src.base.operations.v2.models import AttemptState, OperationRecord, OperationState
from src.base.operations.v2.ownership import (
    ObsoleteFencingToken,
    OwnershipConflict,
)
from src.base.operations.v2.repository import (
    DuplicateOperation,
    OperationNotFound,
    RevisionConflict,
)
from src.base.operations.v2.transitions import finish_attempt, start_attempt
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.genesis import (
    COMPOSITION_SIGNAL,
    GenesisProvisioner,
    _same_store_fact,
    genesis_operation_id,
)
from src.domains.agentverse.agents.genesis.models import (
    GenesisAuditError,
    GenesisOperationInProgress,
    GenesisProvisionRequest,
)
from src.domains.agentverse.audit.repository import MongoLogRepository
from src.domains.agentverse.audit.service import CAOSLogService
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tests.audit.test_caos_log import FakeMongo
from src.domains.agentverse.tests.tools.test_store_provisioned import ENKI_SLOT, FakePersistence
from src.domains.agentverse.tools.store_tool import (
    ProvisionedStoreCapability,
    register_store_create_provisioned,
)
from src.domains.agentverse.tools.store_tool.dependencies.di_container import (
    StoreToolContainer,
)
from src.domains.agentverse.tools.store_tool.models import AmbiguousStoreWrite
from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_PROVISIONED_SIGNAL
from src.domains.agentverse.tools.validation_tool import register_data_validation
from src.domains.agentverse.tools.validation_tool.dependencies.di_container import (
    ValidationToolContainer,
)
from src.domains.agentverse.tools.validation_tool.spec import DATA_VALIDATION_SIGNAL

TENANT, OWNER, GENESIS, ENKI = (
    ENKI_SLOT.tenant_id,
    ENKI_SLOT.subject_id,
    ENKI_SLOT.principal_id,
    ENKI_SLOT.agent_id,
)
GRANTS = {DATA_VALIDATION_SIGNAL, STORE_CREATE_PROVISIONED_SIGNAL}


def creation(**override: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "Enki",
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "prompt": "You are Enki, keeper of CAOS agents.",
    }
    value.update(override)
    return value


def request(**override: Any) -> GenesisProvisionRequest:
    return GenesisProvisionRequest(slot="enki", creation=creation(**override))


class Clock:
    """Millisecond-precision UTC clock (BSON precision required by managed epochs)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=5)
        return self.now


class FakeManagedOperations:
    """In-memory OPS2-04B semantics built from the real models and transitions:
    unique create, CAS activation from pending/retry-pending, fenced outcome commits."""

    def __init__(self, clock: Clock) -> None:
        self.records: dict[UUID, ManagedOperation] = {}
        self.clock = clock

    async def create(self, operation: OperationRecord) -> ManagedOperation:
        if operation.operation_id in self.records:
            raise DuplicateOperation(operation.operation_id)
        record = ManagedOperation(
            schema_version=2,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
        )
        self.records[operation.operation_id] = record
        return record

    async def inspect(self, operation_id: UUID) -> ManagedOperation:
        if operation_id not in self.records:
            raise OperationNotFound(operation_id)
        return self.records[operation_id]

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
    ) -> ManagedOperation:
        record = await self.inspect(operation_id)
        if record.execution.generation != expected_generation:
            raise ObsoleteFencingToken(operation_id)
        if record.snapshot.revision != expected_revision:
            raise RevisionConflict(operation_id)
        if record.snapshot.state not in {OperationState.PENDING, OperationState.RETRY_PENDING}:
            raise OwnershipConflict(operation_id)
        snapshot = start_attempt(record.snapshot, at=at, attempt_id=attempt_id)
        now = self.clock()
        generation = record.execution.generation + 1
        updated = ManagedOperation(
            schema_version=2,
            snapshot=snapshot,
            execution=ExecutionEpoch(
                generation=generation,
                owner_id=owner_id,
                attempt_id=attempt_id,
                status=EpochStatus.ACTIVE,
                mode=EpochMode.EXECUTE,
                acquired_at=now,
                updated_at=now,
                lease_expires_at=now + timedelta(milliseconds=lease_ms),
                released_at=None,
            ),
            last_state_change=self._receipt(
                command_id, "activate", generation, attempt_id, record, snapshot
            ),
        )
        self.records[operation_id] = updated
        return updated

    async def commit_outcome(
        self,
        operation: OperationRecord,
        *,
        expected_revision: int,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        command_id: UUID,
    ) -> ManagedOperation:
        record = await self.inspect(operation.operation_id)
        epoch = record.execution
        if (epoch.generation, epoch.owner_id, epoch.attempt_id) != (
            generation,
            owner_id,
            attempt_id,
        ):
            raise OwnershipConflict(operation.operation_id)
        if epoch.status != EpochStatus.ACTIVE or epoch.mode != EpochMode.EXECUTE:
            raise OwnershipConflict(operation.operation_id)
        if record.snapshot.revision != expected_revision:
            raise RevisionConflict(operation.operation_id)
        now = self.clock()
        status = EpochStatus.CLOSED if operation.state.terminal else EpochStatus.RELEASED
        updated = ManagedOperation(
            schema_version=2,
            snapshot=operation,
            execution=epoch.model_copy(
                update={"status": status, "updated_at": now, "released_at": now}
            ),
            last_state_change=self._receipt(
                command_id, "outcome", generation, attempt_id, record, operation
            ),
        )
        self.records[operation.operation_id] = updated
        return updated

    async def takeover(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        expected_generation: int,
        attempt_id: UUID,
        owner_id: str,
        lease_ms: int,
    ) -> ManagedOperation:
        record = await self.inspect(operation_id)
        epoch = record.execution
        if epoch.generation != expected_generation:
            raise ObsoleteFencingToken(operation_id)
        if record.snapshot.revision != expected_revision:
            raise RevisionConflict(operation_id)
        now = self.clock()
        if (
            record.snapshot.state != OperationState.RUNNING
            or epoch.attempt_id != attempt_id
            or not (epoch.status == EpochStatus.RELEASED or epoch.lease_expires_at <= now)
        ):
            raise OwnershipConflict(operation_id)
        updated = record.model_copy(
            update={
                "execution": ExecutionEpoch(
                    generation=epoch.generation + 1,
                    owner_id=owner_id,
                    attempt_id=attempt_id,
                    status=EpochStatus.ACTIVE,
                    mode=EpochMode.RECONCILE,
                    acquired_at=now,
                    updated_at=now,
                    lease_expires_at=now + timedelta(milliseconds=lease_ms),
                    released_at=None,
                )
            }
        )
        self.records[operation_id] = ManagedOperation.model_validate(updated.model_dump())
        return self.records[operation_id]

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
    ) -> ManagedOperation:
        record = await self.inspect(operation_id)
        epoch = record.execution
        if (epoch.generation, epoch.owner_id, epoch.attempt_id) != (
            generation,
            owner_id,
            attempt_id,
        ):
            raise OwnershipConflict(operation_id)
        if epoch.status != EpochStatus.ACTIVE or epoch.mode != EpochMode.RECONCILE:
            raise OwnershipConflict(operation_id)
        if record.snapshot.revision != expected_revision:
            raise RevisionConflict(operation_id)
        snapshot = finish_attempt(
            record.snapshot,
            attempt_id=attempt_id,
            outcome=AttemptState.FAILED,
            at=at,
            reason=reason,
            retry=True,
        )
        now = self.clock()
        updated = ManagedOperation(
            schema_version=2,
            snapshot=snapshot,
            execution=epoch.model_copy(
                update={"status": EpochStatus.RELEASED, "updated_at": now, "released_at": now}
            ),
            last_state_change=self._receipt(
                command_id, "outcome", generation, attempt_id, record, snapshot
            ),
        )
        self.records[operation_id] = updated
        return updated

    @staticmethod
    def _receipt(command_id, action, generation, attempt_id, before, after) -> StateChangeReceipt:
        return StateChangeReceipt(
            command_id=command_id,
            action=action,
            request_hash="0" * 64,
            generation=generation,
            attempt_id=attempt_id,
            previous_revision=before.snapshot.revision,
            resulting_revision=after.revision,
        )


class Llm:
    async def get_response_async(self, **_: Any) -> str:  # pragma: no cover - never called
        raise AssertionError("Genesis provisioning must not call an LLM")


def registries() -> dict[str, Registry[Any]]:
    r = {kind: Registry(kind) for kind in ("agent", "role", "personality", "rulepack", "tool")}
    for kind, key, meta in (
        ("agent", "chat", {}),
        ("agent", "form", {}),
        ("role", "general", {}),
        ("role", "writer", {"capabilities": {"required_tools": {"probe": "Probe"}}}),
        ("personality", "empty_eva", {}),
        ("tool", "probe", {}),
    ):
        r[kind].register(
            name=key, key=key, component=object, metadata={"ownership": "global", **meta}
        )
    return r


def stack(
    *,
    grants: set[str] = frozenset(GRANTS),
    key: AgentInstanceKey | None = None,
    catalog: Any = "default",
    operations: Any = None,
    log_repository: Any = None,
) -> SimpleNamespace:
    key = key or AgentInstanceKey(TENANT, OWNER, GENESIS, "operator-session")
    policy = SynapticAuthorization(grants={(key.tenant_id, key.agent_id): SynapticGrants(grants)})
    versions = (policy_version(policy), "incarnation", "0", "0")
    revocations = SimpleNamespace(read=AsyncMock(return_value=versions))
    utu = UTUAuthority(
        resolver=SimpleNamespace(_sessions=SimpleNamespace(), _definitions=SimpleNamespace()),
        authorization=policy,
        revocations=revocations,
    )
    persistence = FakePersistence()
    store = StoreToolContainer(
        mongodb_repository=providers.Object(Mock()), utu_authority=providers.Object(utu)
    )
    store.agent_definition_repository.override(providers.Object(persistence))
    validation = ValidationToolContainer(
        registries=providers.Object(registries()),
        workflow_service=providers.Object(SimpleNamespace()),
        utu_authority=providers.Object(utu),
    )
    registry = SynapticRegistry()
    register_data_validation(registry, validation.tool())
    register_store_create_provisioned(
        registry, ProvisionedStoreCapability(tool=store.tool(), provisions={"enki": ENKI_SLOT})
    )
    exchange = SynapticExchange(registry=registry, authorization=utu)
    client = exchange.bind(
        AuthorizedAgentPrincipal(key.agent_id, key.tenant_id, ExecutionPermit(key, versions))
    )
    clock = Clock()
    if catalog == "default":
        catalog = ClientFactory({"llm": {"model": Llm()}}, {"llm": "model"})
    mongo = FakeMongo()
    log = CAOSLogService(repository=log_repository or MongoLogRepository(mongo), clock=clock)
    holder = SimpleNamespace(catalog=catalog)
    operations = operations or FakeManagedOperations(clock)
    provisioner = GenesisProvisioner(
        operations=operations,
        log=log,
        composer=AgentComposer(catalog=lambda: holder.catalog),
        authority=utu,
        clock=clock,
    )
    return SimpleNamespace(
        client=client,
        exchange=exchange,
        provisioner=provisioner,
        persistence=persistence,
        operations=operations,
        log=log,
        logs=mongo.collection,
        holder=holder,
        revocations=revocations,
        key=key,
        permit=client.execution_permit,
    )


async def history(s: SimpleNamespace, operation_id: UUID, tenant: str = TENANT) -> list[Any]:
    return await s.log.history(tenant, str(operation_id))


def state(s: SimpleNamespace, operation_id: UUID) -> OperationState:
    return s.operations.records[operation_id].snapshot.state


# --- Success and reconstruction --------------------------------------------------------


@pytest.mark.asyncio
async def test_genesis_provisions_enki_and_records_reconstructable_evidence() -> None:
    s = stack()
    result = await s.provisioner.provision(s.client, request())
    assert (result.status, result.agent_id, result.created, result.replayed) == (
        "provisioned",
        ENKI,
        True,
        False,
    )
    assert result.operation_id == genesis_operation_id(s.permit, request())
    assert result.correlation_id == str(result.operation_id)
    (document,) = s.persistence.documents
    assert (document["id"], document["tenant_id"], document["creator"]) == (ENKI, TENANT, OWNER)

    records = await history(s, result.operation_id)
    assert [r.event_type for r in records] == [
        "genesis.validation.completed",
        "genesis.composition.completed",
        "genesis.store.completed",
    ]
    assert [r.event_id for r in records] == list(result.audit_event_ids)
    validation, composition, stored = records
    # Causation reconstructs the order: operation -> validation -> composition -> store.
    assert validation.causation_id == result.correlation_id
    assert composition.causation_id == validation.event_id
    assert stored.causation_id == composition.event_id
    assert {r.correlation_id for r in records} == {result.correlation_id}
    assert {r.operation_id for r in records} == {result.operation_id}
    # The created agent is linked to its creation operation and to Store's own record.
    assert stored.subject.resource_id == ENKI
    assert stored.metadata["request_digest"] == document["store"]["request_digest"]
    assert composition.metadata["validation_digest"] == validation.metadata["normalized_digest"]
    operation = s.operations.records[result.operation_id].snapshot
    assert operation.state == OperationState.SUCCEEDED and len(operation.attempts) == 1


@pytest.mark.asyncio
async def test_every_record_is_attributed_to_the_verified_permit_and_executed_signal() -> None:
    s = stack()
    result = await s.provisioner.provision(s.client, request())
    records = await history(s, result.operation_id)
    signals = [DATA_VALIDATION_SIGNAL, COMPOSITION_SIGNAL, STORE_CREATE_PROVISIONED_SIGNAL]
    for record, signal in zip(records, signals, strict=True):
        assert record.tenant_id == TENANT
        assert (record.actor.principal_id, record.actor.user_id) == (GENESIS, OWNER)
        assert record.target.signal == signal
        assert record.authorization == permit_provenance(s.permit, signal)
    assert "operator-session" not in repr([r.model_dump() for r in records])


# --- Retries ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_retry_replays_without_duplicating_records_agents_or_effects() -> None:
    s = stack()
    first = await s.provisioner.provision(s.client, request())
    calls, logs = list(s.persistence.calls), dict(s.logs.documents)
    again = await s.provisioner.provision(s.client, request())
    assert again.replayed and (again.status, again.agent_id, again.created) == (
        "provisioned",
        ENKI,
        True,
    )
    assert again.operation_id == first.operation_id
    assert again.audit_event_ids == first.audit_event_ids
    assert s.persistence.calls == calls and len(s.persistence.documents) == 1
    assert s.logs.documents == logs
    assert len(s.operations.records[first.operation_id].snapshot.attempts) == 1


@pytest.mark.asyncio
async def test_conflicting_retry_is_rejected_by_store_and_audited() -> None:
    s = stack()
    await s.provisioner.provision(s.client, request())
    conflicting = await s.provisioner.provision(s.client, request(prompt="A different Enki."))
    assert (conflicting.status, conflicting.stage, conflicting.issue_codes) == (
        "rejected",
        "store",
        ("STORE_IDEMPOTENCY_CONFLICT",),
    )
    records = await history(s, conflicting.operation_id)
    assert records[-1].event_type == "genesis.store.failed"
    assert records[-1].outcome == "rejected"
    assert state(s, conflicting.operation_id) == OperationState.FAILED
    (document,) = s.persistence.documents
    assert document["prompt"] == "You are Enki, keeper of CAOS agents."


# --- Authorization ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_principal_without_genesis_grants_cannot_create() -> None:
    s = stack(grants={"chat.context"})
    with pytest.raises(EventPermissionError):
        await s.provisioner.provision(s.client, request())
    assert s.persistence.documents == [] and s.logs.documents == {}
    (record,) = s.operations.records.values()
    assert record.snapshot.state == OperationState.RETRY_PENDING  # never succeeded


@pytest.mark.asyncio
async def test_principal_outside_the_pinned_slot_cannot_create() -> None:
    s = stack(key=AgentInstanceKey(TENANT, OWNER, "other-principal", "s"))
    with pytest.raises(EventPermissionError):
        await s.provisioner.provision(s.client, request())
    assert s.persistence.documents == []
    assert not any(d["event_type"].startswith("genesis.store") for d in s.logs.documents.values())


@pytest.mark.asyncio
async def test_revoked_permit_or_non_cpbx_client_is_refused_before_any_state() -> None:
    s = stack()
    s.revocations.read.return_value = ("revoked", "incarnation", "0", "0")
    with pytest.raises(EventPermissionError):
        await s.provisioner.provision(s.client, request())
    forged = SimpleNamespace(execution_permit=s.permit)
    with pytest.raises(EventPermissionError):
        await s.provisioner.provision(forged, request())  # type: ignore[arg-type]
    assert s.operations.records == {} and s.logs.documents == {}


@pytest.mark.asyncio
async def test_request_cannot_carry_identity_or_authority() -> None:
    s = stack()
    for bad in (
        {"slot": "enki", "creation": creation(), "tenant_id": TENANT},
        {"slot": "enki", "creation": creation(), "authorization": {}},
        {"slot": "Enki", "creation": creation()},
    ):
        with pytest.raises(ValueError):
            await s.provisioner.provision(s.client, bad)  # type: ignore[arg-type]
    assert s.operations.records == {}


# --- Downstream stops ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_rejection_stops_before_composition_and_store() -> None:
    s = stack()
    payload = creation()
    del payload["prompt"]
    result = await s.provisioner.provision(
        s.client, GenesisProvisionRequest(slot="enki", creation=payload)
    )
    assert (result.status, result.stage, result.issue_codes) == (
        "rejected",
        "validation",
        ("field.required",),
    )
    (record,) = await history(s, result.operation_id)
    assert (record.event_type, record.outcome) == ("genesis.validation.completed", "rejected")
    assert s.persistence.calls == []
    assert state(s, result.operation_id) == OperationState.FAILED
    replay = await s.provisioner.provision(
        s.client, GenesisProvisionRequest(slot="enki", creation=payload)
    )
    assert replay.replayed and (replay.status, replay.stage) == ("rejected", "validation")


@pytest.mark.asyncio
async def test_definitive_composition_rejection_stops_before_store() -> None:
    s = stack()
    result = await s.provisioner.provision(
        s.client, request(role_name="writer", tools=[{"key": "probe"}])
    )
    assert (result.status, result.stage, result.issue_codes) == (
        "rejected",
        "composition",
        ("composition.role_tools_unsupported",),
    )
    assert s.persistence.calls == []
    assert state(s, result.operation_id) == OperationState.FAILED


@pytest.mark.asyncio
async def test_transient_composition_failure_is_retryable_and_then_succeeds() -> None:
    s = stack(catalog=None)
    failed = await s.provisioner.provision(s.client, request())
    assert (failed.status, failed.stage, failed.retryable) == ("failed", "composition", True)
    assert state(s, failed.operation_id) == OperationState.RETRY_PENDING
    assert s.persistence.calls == []
    s.holder.catalog = ClientFactory({"llm": {"model": Llm()}}, {"llm": "model"})
    done = await s.provisioner.provision(s.client, request())
    assert (done.status, done.agent_id, done.operation_id) == (
        "provisioned",
        ENKI,
        failed.operation_id,
    )
    records = await history(s, done.operation_id)
    assert [(r.event_type, r.outcome) for r in records] == [
        ("genesis.validation.completed", "succeeded"),
        ("genesis.composition.completed", "rejected"),  # attempt 1
        ("genesis.composition.completed", "succeeded"),
        ("genesis.store.completed", "succeeded"),
    ]


# --- Store failure ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_failure_is_audited_and_retry_completes() -> None:
    s = stack()
    original = s.persistence.insert_once

    async def ambiguous(document: dict[str, Any]) -> Any:
        raise AmbiguousStoreWrite("Agent write outcome is unknown")

    s.persistence.insert_once = ambiguous
    failed = await s.provisioner.provision(s.client, request())
    assert (failed.status, failed.stage, failed.retryable, failed.issue_codes) == (
        "failed",
        "store",
        True,
        ("STORE_AMBIGUOUS_WRITE",),
    )
    records = await history(s, failed.operation_id)
    assert (records[-1].event_type, records[-1].outcome) == ("genesis.store.failed", "failed")
    assert records[-1].metadata == {
        "resource_type": "agent",
        "code": "STORE_AMBIGUOUS_WRITE",
        "retryable": True,
    }
    assert state(s, failed.operation_id) == OperationState.RETRY_PENDING
    s.persistence.insert_once = original
    done = await s.provisioner.provision(s.client, request())
    assert done.status == "provisioned" and len(s.persistence.documents) == 1


# --- Audit durability ---------------------------------------------------------------------


def fail_log_insert(s: SimpleNamespace, *, nth: int, commit_then_fail: bool = False) -> None:
    """Fail the nth Seshat insert (1-based) once."""
    collection = s.logs
    original = collection.insert_one
    seen = {"count": 0}

    async def insert_one(document: dict[str, Any]) -> Any:
        seen["count"] += 1
        if seen["count"] == nth:
            if commit_then_fail:
                collection.commit_then_fail = True
                try:
                    return await original(document)
                finally:
                    collection.commit_then_fail = False
            from pymongo.errors import AutoReconnect

            raise AutoReconnect("audit store down")
        return await original(document)

    collection.insert_one = insert_one


@pytest.mark.asyncio
async def test_audit_failure_before_store_propagates_and_prevents_the_write() -> None:
    s = stack()
    fail_log_insert(s, nth=1)
    with pytest.raises(GenesisAuditError) as excinfo:
        await s.provisioner.provision(s.client, request())
    assert excinfo.value.retryable is True
    assert s.persistence.calls == []
    assert state(s, excinfo.value.operation_id) == OperationState.RETRY_PENDING
    assert (await s.provisioner.provision(s.client, request())).status == "provisioned"


@pytest.mark.asyncio
async def test_store_write_without_audit_is_detected_and_reconciled_by_retry() -> None:
    s = stack()
    fail_log_insert(s, nth=3)  # store.completed evidence fails after Store committed
    with pytest.raises(GenesisAuditError) as excinfo:
        await s.provisioner.provision(s.client, request())
    operation_id = excinfo.value.operation_id
    # Detected: Enki is persisted, but the operation never claims success without evidence.
    assert len(s.persistence.documents) == 1
    assert state(s, operation_id) == OperationState.RETRY_PENDING
    assert "genesis.store.completed" not in [r.event_type for r in await history(s, operation_id)]
    # Reconciled: the same request replays Store idempotently and records the evidence.
    done = await s.provisioner.provision(s.client, request())
    assert (done.status, done.agent_id, done.created) == ("provisioned", ENKI, False)
    assert len(s.persistence.documents) == 1
    records = await history(s, operation_id)
    assert [r.event_type for r in records] == [
        "genesis.validation.completed",
        "genesis.composition.completed",
        "genesis.store.completed",
    ]
    assert state(s, operation_id) == OperationState.SUCCEEDED


@pytest.mark.asyncio
async def test_ambiguous_audit_write_that_committed_is_reconciled_to_the_original_fact() -> None:
    s = stack()
    fail_log_insert(s, nth=3, commit_then_fail=True)
    with pytest.raises(GenesisAuditError):
        await s.provisioner.provision(s.client, request())
    done = await s.provisioner.provision(s.client, request())
    # The retry's store replay reports created=False, but the durable evidence of the
    # first attempt (created=True) is the recorded fact; nothing is overwritten.
    assert (done.status, done.created) == ("provisioned", True)
    stored = [
        r for r in await history(s, done.operation_id) if r.event_type == "genesis.store.completed"
    ]
    assert len(stored) == 1 and stored[0].metadata["created"] is True


def test_only_an_equivalent_store_fact_is_accepted_on_conflict() -> None:
    from src.domains.agentverse.audit.models import CAOSLogRecord, LogEvent

    provenance = permit_provenance(
        ExecutionPermit(AgentInstanceKey(TENANT, OWNER, GENESIS, "s"), ("p",)), "x"
    )
    base = {
        "event_id": "e",
        "event_type": "genesis.store.completed",
        "outcome": "succeeded",
        "subject": {"resource_type": "agent", "resource_id": ENKI},
        "target": {"signal": STORE_CREATE_PROVISIONED_SIGNAL, "version": "1.0.0"},
        "correlation": {"correlation_id": "c", "causation_id": "k"},
        "metadata": {
            "resource_type": "agent",
            "created": True,
            "request_digest": "a" * 64,
            "slot": "enki",
        },
    }
    stored = CAOSLogRecord.build(
        LogEvent.model_validate(base), authorization=provenance, recorded_at=datetime.now(UTC)
    )
    replay = LogEvent.model_validate({**base, "metadata": {**base["metadata"], "created": False}})
    assert _same_store_fact(stored, replay, provenance)
    other_digest = LogEvent.model_validate(
        {**base, "metadata": {**base["metadata"], "request_digest": "b" * 64}}
    )
    other_agent = LogEvent.model_validate(
        {**base, "subject": {"resource_type": "agent", "resource_id": "x"}}
    )
    for event in (other_digest, other_agent):
        assert not _same_store_fact(stored, event, provenance)
    other_user = permit_provenance(
        ExecutionPermit(AgentInstanceKey(TENANT, "u2", GENESIS, "s"), ("p",)), "x"
    )
    assert not _same_store_fact(stored, replay, other_user)
    validation = LogEvent.model_validate(
        {
            **base,
            "event_type": "genesis.validation.completed",
            "metadata": {"resource_type": "agent", "valid": True},
        }
    )
    assert not _same_store_fact(stored, validation, provenance)


# --- Interruption and concurrency -----------------------------------------------------------


async def _crashed_attempt(s: SimpleNamespace, *, lease_ms: int | None = None) -> UUID:
    """An earlier process activated an attempt and died without any outcome."""
    operation_id = genesis_operation_id(s.permit, request())
    record = await s.operations.create(
        OperationRecord.create(
            kind="genesis.provision",
            producer="genesis",
            at=s.operations.clock(),
            operation_id=operation_id,
        )
    )
    await s.operations.activate(
        operation_id,
        expected_revision=record.snapshot.revision,
        expected_generation=0,
        attempt_id=uuid4(),
        owner_id="genesis:crashed",
        lease_ms=lease_ms or 600_000,
        at=s.operations.clock(),
        command_id=uuid4(),
    )
    return operation_id


@pytest.mark.asyncio
async def test_live_attempt_is_reported_never_taken_over() -> None:
    s = stack()
    await _crashed_attempt(s)  # lease still live: it may be running elsewhere
    with pytest.raises(GenesisOperationInProgress) as excinfo:
        await s.provisioner.provision(s.client, request())
    assert excinfo.value.interrupted is False
    assert s.persistence.calls == [] and s.logs.documents == {}


@pytest.mark.asyncio
async def test_expired_attempt_is_reconciled_then_executed_by_a_fresh_attempt() -> None:
    s = stack()
    operation_id = await _crashed_attempt(s, lease_ms=1)
    result = await s.provisioner.provision(s.client, request())
    assert (result.status, result.agent_id, result.operation_id) == (
        "provisioned",
        ENKI,
        operation_id,
    )
    attempts = s.operations.records[operation_id].snapshot.attempts
    assert [(a.state, a.reason) for a in attempts] == [
        (AttemptState.FAILED, "interrupted attempt reconciled by genesis"),
        (AttemptState.SUCCEEDED, None),
    ]


class ProcessDied(BaseException):
    """Simulated process death: bypasses every handler, no outcome is committed."""


def crash_before_final_audit(s: SimpleNamespace) -> None:
    """Store succeeds; the process dies before the store.completed evidence is written."""
    repository = s.log._repository
    original = repository.append
    calls = {"n": 0}

    async def append(record: Any) -> Any:
        calls["n"] += 1
        if record.event_type == "genesis.store.completed":
            raise ProcessDied
        return await original(record)

    async def no_outcome(*_: Any, **__: Any) -> Any:
        raise ProcessDied

    repository.append = append
    s.operations.commit_outcome = no_outcome

    def restore() -> None:
        repository.append = original
        del s.operations.commit_outcome  # back to the class method

    s.restore = restore


async def recover_after_crash(s: SimpleNamespace, *, wait: Any) -> None:
    crash_before_final_audit(s)
    with pytest.raises(ProcessDied):
        await s.provisioner.provision(s.client, request())
    operation_id = genesis_operation_id(s.permit, request())
    assert len(s.persistence.documents) == 1  # Enki was persisted
    before = await history(s, operation_id)
    assert [r.event_type for r in before] == [
        "genesis.validation.completed",
        "genesis.composition.completed",
    ]
    assert (await s.operations.inspect(operation_id)).snapshot.state == OperationState.RUNNING
    s.restore()
    await wait()
    # A new process (new owner identity) receives the identical request.
    recovered = GenesisProvisioner(
        operations=s.operations,
        log=s.log,
        composer=s.provisioner._composer,
        authority=s.provisioner._authority,
        clock=s.provisioner._clock,
        lease_ms=s.provisioner._lease_ms,
    )
    result = await recovered.provision(s.client, request())
    # Store replayed; the missing evidence is the store's actual replay observation,
    # not a fabricated "created" record.
    assert (result.status, result.agent_id, result.created) == ("provisioned", ENKI, False)
    assert len(s.persistence.documents) == 1
    after = await history(s, operation_id)
    assert [r.event_type for r in after] == [
        "genesis.validation.completed",
        "genesis.composition.completed",
        "genesis.store.completed",
    ]
    assert after[:2] == before  # verified records preserved exactly (IDs, times, content)
    assert (
        after[2].metadata["request_digest"] == s.persistence.documents[0]["store"]["request_digest"]
    )
    snapshot = (await s.operations.inspect(operation_id)).snapshot
    assert snapshot.state == OperationState.SUCCEEDED
    assert [a.state for a in snapshot.attempts] == [AttemptState.FAILED, AttemptState.SUCCEEDED]


@pytest.mark.asyncio
async def test_crash_between_store_and_final_audit_is_detected_and_reconciled() -> None:
    s = stack()

    async def expire() -> None:
        s.operations.clock.now += timedelta(milliseconds=s.provisioner._lease_ms + 1)

    await recover_after_crash(s, wait=expire)


@pytest.mark.asyncio
async def test_concurrent_identical_requests_create_one_agent_and_one_history() -> None:
    s = stack()
    results = await asyncio.gather(
        s.provisioner.provision(s.client, request()),
        s.provisioner.provision(s.client, request()),
        return_exceptions=True,
    )
    succeeded = [r for r in results if not isinstance(r, BaseException)]
    assert len(succeeded) >= 1
    assert all(
        isinstance(r, GenesisOperationInProgress) for r in results if isinstance(r, BaseException)
    )
    assert len(s.persistence.documents) == 1
    assert len(s.logs.documents) == 3


# --- Tenant isolation and identity ----------------------------------------------------------


@pytest.mark.asyncio
async def test_operation_identity_and_history_are_tenant_and_subject_scoped() -> None:
    s = stack()
    result = await s.provisioner.provision(s.client, request())
    assert await history(s, result.operation_id, tenant="tenant-b") == []
    permit = s.permit
    other_tenant = ExecutionPermit(
        AgentInstanceKey("tenant-b", OWNER, GENESIS, "s"), permit.versions
    )
    other_user = ExecutionPermit(AgentInstanceKey(TENANT, "u2", GENESIS, "s"), permit.versions)
    ids = {
        genesis_operation_id(permit, request()),
        genesis_operation_id(other_tenant, request()),
        genesis_operation_id(other_user, request()),
        genesis_operation_id(permit, request(prompt="different")),
        genesis_operation_id(permit, GenesisProvisionRequest(slot="adam", creation=creation())),
    }
    assert len(ids) == 5
    assert genesis_operation_id(permit, request()) == result.operation_id


# --- Real MongoDB ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mongo() -> AsyncIterator[tuple[Any, str]]:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo Genesis tests")
    from src.base.dependencies.containers.db_container import DatabaseContainer

    database = DatabaseContainer()
    infrastructure = database.mongodb_repository()
    suffix = uuid4().hex
    names = (f"genesis_ops_test_{suffix}", f"genesis_logs_test_{suffix}")
    try:
        yield infrastructure, names
    finally:
        try:
            for name in names:
                await (await infrastructure.get_collection(name)).drop()
        finally:
            await database.mongo_client().disconnect()


@pytest.mark.asyncio
async def test_mongo_genesis_provision_replay_and_audit_reconciliation(mongo: Any) -> None:
    from src.base.operations.v2.mongo_managed_repository import MongoManagedOperationRepository

    infrastructure, (ops_name, logs_name) = mongo
    s = stack(
        operations=MongoManagedOperationRepository(infrastructure, ops_name),
        log_repository=MongoLogRepository(infrastructure, logs_name),
    )
    s.provisioner._clock = lambda: datetime.now(UTC)
    # Store committed, evidence lost: detected, then reconciled by the identical retry.
    from src.domains.agentverse.audit.repository import AmbiguousLogWrite

    original = s.log._repository.append
    calls = {"n": 0}

    async def flaky(record: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise AmbiguousLogWrite("simulated")
        return await original(record)

    s.log._repository.append = flaky
    with pytest.raises(GenesisAuditError) as excinfo:
        await s.provisioner.provision(s.client, request())
    operation = await s.operations.inspect(excinfo.value.operation_id)
    assert operation.snapshot.state == OperationState.RETRY_PENDING
    done = await s.provisioner.provision(s.client, request())
    assert (done.status, done.agent_id) == ("provisioned", ENKI)
    again = await s.provisioner.provision(s.client, request())
    assert again.replayed and again.audit_event_ids == done.audit_event_ids
    operation = await s.operations.inspect(done.operation_id)
    assert operation.snapshot.state == OperationState.SUCCEEDED
    assert [a.state.value for a in operation.snapshot.attempts] == ["failed", "succeeded"]
    assert len(s.persistence.documents) == 1


# --- Scalability invariant: operation-centric, bounded audit --------------------------------


@pytest.mark.asyncio
async def test_successful_genesis_writes_exactly_one_record_per_audited_transition() -> None:
    s = stack()
    inserts = {"n": 0}
    original = s.logs.insert_one

    async def counting(document: dict[str, Any]) -> Any:
        inserts["n"] += 1
        return await original(document)

    s.logs.insert_one = counting
    await s.provisioner.provision(s.client, request())
    # Two CPBX invocations (each with its own synapse/child context) and Store's own
    # writes produce no audit documents; only the three audited transitions do.
    assert inserts["n"] == 3
    await s.provisioner.provision(s.client, request())  # completed: answered from evidence
    assert inserts["n"] == 3


@pytest.mark.asyncio
async def test_repeated_failures_get_distinct_ids_while_retried_successes_keep_theirs() -> None:
    s = stack(catalog=None)
    first = await s.provisioner.provision(s.client, request())
    second = await s.provisioner.provision(s.client, request())
    assert first.retryable and second.retryable
    validation_id = first.audit_event_ids[0]
    assert second.audit_event_ids[0] == validation_id  # same logical step, same identity
    assert first.audit_event_ids[1] != second.audit_event_ids[1]  # attempt 1 vs attempt 2
    s.holder.catalog = ClientFactory({"llm": {"model": Llm()}}, {"llm": "model"})
    done = await s.provisioner.provision(s.client, request())
    records = await history(s, done.operation_id)
    assert [(r.event_type, r.outcome) for r in records] == [
        ("genesis.validation.completed", "succeeded"),
        ("genesis.composition.completed", "rejected"),
        ("genesis.composition.completed", "rejected"),
        ("genesis.composition.completed", "succeeded"),
        ("genesis.store.completed", "succeeded"),
    ]
    assert len({r.event_id for r in records}) == 5
    assert [
        a.attempt_number for a in s.operations.records[done.operation_id].snapshot.attempts
    ] == [
        1,
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_mongo_crash_between_store_and_final_audit_is_reconciled(mongo: Any) -> None:
    from src.base.operations.v2.mongo_managed_repository import MongoManagedOperationRepository

    infrastructure, (ops_name, logs_name) = mongo
    s = stack(
        operations=MongoManagedOperationRepository(infrastructure, ops_name),
        log_repository=MongoLogRepository(infrastructure, logs_name),
    )
    s.provisioner._clock = lambda: datetime.now(UTC)
    s.provisioner._lease_ms = 300  # the crashed attempt's lease, enforced by Mongo $$NOW

    async def expire() -> None:
        await asyncio.sleep(0.5)

    await recover_after_crash(s, wait=expire)
