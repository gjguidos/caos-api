"""OPS2-04C.1 admission/discovery contracts and real-Mongo atomicity/index tests."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from pymongo.errors import AutoReconnect, OperationFailure

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import CognitiveEventPolicy, EventGrants
from src.base.events.v2.registry import CognitiveEventRegistry
from src.base.events.v2.runtime import CognitiveEventRuntime
from src.base.operations.v2.admission import (
    ADMISSION_PRODUCER,
    MANUAL_REVIEW_V1,
    AdmissionRecord,
    AuthorizationProvenance,
    CapabilityRef,
    DiscoveryRequest,
    ExecutionDescriptor,
    IdempotencyConflict,
    InlineInput,
    InputReference,
    InvalidAdmission,
    RecoveryPolicyRef,
    UnapprovedRecoveryPolicy,
    UnknownCapability,
    discovery_key,
    idempotency_scope,
)
from src.base.operations.v2.admission_service import (
    AdmissionRequest,
    OperationAdmissionService,
)
from src.base.operations.v2.dispatch import (
    DISPATCH_EVENT_TYPE,
    CognitiveEventDispatchNotifier,
    OperationDispatchNotice,
    register_dispatch_contract,
)
from src.base.operations.v2.managed import (
    AmbiguousManagedWrite,
    CorruptManagedOperation,
    EpochStatus,
    ExecutionEpoch,
    ManagedOperation,
)
from src.base.operations.v2.models import OperationRecord, OperationState
from src.base.operations.v2.mongo_admission_repository import (
    PENDING_INDEX,
    TENANT_PENDING_INDEX,
    MongoOperationAdmissionRepository,
)
from src.base.operations.v2.mongo_managed_repository import (
    MongoManagedOperationRepository,
    managed_from_document,
    managed_to_document,
)
from src.base.operations.v2.mongo_repository import MongoOperationRepository
from src.base.operations.v2.ownership import ObsoleteFencingToken, OwnershipConflict
from src.base.operations.v2.repository import CorruptSnapshot, RevisionConflict
from src.base.operations.v2.transitions import start_attempt

T0 = datetime(2026, 10, 29, tzinfo=UTC)
WEATHER = CapabilityRef(
    signal="weather.forecast.fetch",
    version="1.0.0",
    provider="tool.weather",
    executable="domains.weather.forecast:fetch",
)


def provenance(tenant: str = "tenant-a", principal: str = "agent.adam") -> AuthorizationProvenance:
    return AuthorizationProvenance(
        authority="utu",
        tenant_id=tenant,
        principal_id=principal,
        subject_id="user-1",
        policy_version="p" * 8,
        evidence_digest="e" * 64,
    )


def descriptor(**overrides: Any) -> ExecutionDescriptor:
    authorization = overrides.pop("authorization", provenance())
    values: dict[str, Any] = {
        "descriptor_version": 1,
        "capability": WEATHER,
        "input": InlineInput.from_value({"city": "Lisbon", "days": 3}),
        "tenant_id": authorization.tenant_id,
        "requester_id": authorization.principal_id,
        "authorization": authorization,
        "recovery_policy": MANUAL_REVIEW_V1,
        "correlation_id": "corr-1",
        "causation_id": "cause-1",
    }
    values.update(overrides)
    return ExecutionDescriptor(**values)


def admitted(
    *, operation_id: UUID | None = None, at: datetime = T0, key: str = "k1", **overrides: Any
) -> tuple[OperationRecord, AdmissionRecord]:
    value = descriptor(**overrides)
    operation = OperationRecord.create(
        operation_id=operation_id,
        kind=value.capability.signal,
        producer=ADMISSION_PRODUCER,
        at=at,
        correlation_id=value.correlation_id,
        causation_id=value.causation_id,
    )
    return operation, AdmissionRecord(
        descriptor=value,
        idempotency_scope=idempotency_scope(value.tenant_id, value.requester_id, key),
        discovery_key=discovery_key(operation),
    )


def aggregate(**overrides: Any) -> ManagedOperation:
    operation, admission = admitted(**overrides)
    return ManagedOperation(
        schema_version=3,
        snapshot=operation,
        execution=ExecutionEpoch.idle(),
        last_state_change=None,
        admission=admission,
    )


class Catalog:
    def __init__(self, *refs: CapabilityRef) -> None:
        self.refs = {(ref.signal, ref.version): ref for ref in refs}

    def resolve(self, signal: str, version: str) -> CapabilityRef:
        return self.refs[(signal, version)]


class Notifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.notices: list[OperationDispatchNotice] = []

    async def notify(self, notice: OperationDispatchNotice) -> None:
        self.notices.append(notice)
        if self.error is not None:
            raise self.error


def request(**overrides: Any) -> AdmissionRequest:
    values: dict[str, Any] = {
        "signal": WEATHER.signal,
        "version": WEATHER.version,
        "input": {"city": "Lisbon", "days": 3},
        "idempotency_key": "request-1",
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return AdmissionRequest(**values)


# --- Pure contracts -------------------------------------------------------------------


def test_descriptor_is_immutable_and_reconstructs_exactly() -> None:
    value = descriptor()
    restored = ExecutionDescriptor.model_validate_json(value.model_dump_json(), strict=True)
    assert restored == value and restored.digest == value.digest
    with pytest.raises(ValidationError):
        value.tenant_id = "tenant-b"  # type: ignore[misc]
    inline = value.input
    assert isinstance(inline, InlineInput)
    inline.value["city"] = "Porto"  # Copies cannot alter the stored canonical text.
    assert inline.value == {"city": "Lisbon", "days": 3}


def test_descriptor_holds_no_permit_or_secret_fields() -> None:
    names = set(ExecutionDescriptor.model_fields) | set(AuthorizationProvenance.model_fields)
    assert not names & {"permit", "token", "jwt", "secret", "credentials", "session_id"}
    with pytest.raises(ValidationError):
        AuthorizationProvenance(**provenance().model_dump(), permit=object())


def test_descriptor_identity_must_match_authorization() -> None:
    with pytest.raises(ValidationError):
        descriptor(tenant_id="tenant-b")
    with pytest.raises(ValidationError):
        descriptor(requester_id="agent.other")


@pytest.mark.parametrize("value", [{"x": {1, 2}}, {"x": float("nan")}, {1: "x"}, ["list"]])
def test_inline_input_requires_plain_json_object(value: Any) -> None:
    with pytest.raises(InvalidAdmission):
        InlineInput.from_value(value)


def test_inline_input_digest_and_size_are_validated() -> None:
    good = InlineInput.from_value({"a": 1})
    with pytest.raises(ValidationError):
        InlineInput(kind="inline", canonical=good.canonical, digest="0" * 64)
    with pytest.raises(ValidationError):
        InlineInput(kind="inline", canonical='{"b":1, "a":2}', digest=good.digest)
    with pytest.raises(ValidationError):
        InlineInput.from_value({"blob": "x" * (64 * 1024)})


@pytest.mark.parametrize(
    "uri",
    [
        "https://user:pw@store/blob",
        "https://store/blob?sig=secret",
        "https://store/blob#frag",
        "no-scheme",
    ],
)
def test_input_reference_rejects_credential_bearing_uris(uri: str) -> None:
    with pytest.raises(ValidationError):
        InputReference(kind="reference", uri=uri, digest="a" * 64, size_bytes=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("signal", "Weather"),
        ("signal", "weather"),
        ("version", ""),
        ("version", " 1.0.0"),
        ("provider", ""),
        ("executable", "x" * 300),
    ],
)
def test_invalid_capability_metadata_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        CapabilityRef(**{**WEATHER.model_dump(), field: value})


def test_schema_versions_are_explicit_and_admission_bound() -> None:
    record = aggregate()
    operation, admission = admitted()
    with pytest.raises(ValidationError):
        ManagedOperation(
            schema_version=2,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
            admission=admission,
        )
    with pytest.raises(ValidationError):
        ManagedOperation(
            schema_version=3,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
        )
    assert record.admission is not None
    for change in (
        {"kind": "other.kind"},
        {"producer": "someone.else"},
        {"correlation_id": "forged"},
        {"causation_id": None},
    ):
        values = {
            "operation_id": operation.operation_id,
            "kind": operation.kind,
            "producer": ADMISSION_PRODUCER,
            "at": T0,
            "correlation_id": operation.correlation_id,
            "causation_id": operation.causation_id,
            **change,
        }
        with pytest.raises(ValidationError):
            ManagedOperation(
                schema_version=3,
                snapshot=OperationRecord.create(**values),
                execution=ExecutionEpoch.idle(),
                last_state_change=None,
                admission=admission,
            )
    wrong_key = admission.model_copy(update={"discovery_key": f"{0:020d}:{uuid4()}"})
    with pytest.raises(ValidationError):
        ManagedOperation(
            schema_version=3,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
            admission=wrong_key,
        )


def test_v2_documents_keep_their_exact_shape() -> None:
    operation = OperationRecord.create(kind="test", producer="test", at=T0)
    v2 = ManagedOperation(
        schema_version=2,
        snapshot=operation,
        execution=ExecutionEpoch.idle(),
        last_state_change=None,
    )
    document = managed_to_document(v2)
    assert "admission" not in document
    assert managed_from_document(document) == v2
    with pytest.raises(CorruptManagedOperation):
        managed_from_document({**document, "admission": None})


def test_v3_restoration_is_strict() -> None:
    document = managed_to_document(aggregate())
    assert managed_from_document(document) == aggregate(operation_id=UUID(document["_id"]))

    def broken(mutate: Any) -> dict[str, Any]:
        copy = json.loads(json.dumps(document, default=str))
        copy["execution"] = document["execution"]
        mutate(copy)
        return copy

    cases = [
        lambda d: d.pop("admission"),
        lambda d: d["admission"].update(extra=1),
        lambda d: d["admission"]["descriptor"].pop("recovery_policy"),
        lambda d: d["admission"]["descriptor"]["capability"].update(extra=1),
        lambda d: d["admission"]["descriptor"]["authorization"].update(permit="x"),
        lambda d: d["admission"]["descriptor"]["input"].update(digest="0" * 64),
        lambda d: d["admission"]["descriptor"].update(descriptor_version=2),
        lambda d: d.update(schema_version=4),
    ]
    for mutate in cases:
        with pytest.raises(CorruptManagedOperation):
            managed_from_document(broken(mutate))


def test_idempotency_scope_separates_tenant_and_requester() -> None:
    assert idempotency_scope("a", "p", "k") != idempotency_scope("b", "p", "k")
    assert idempotency_scope("a", "p", "k") != idempotency_scope("a", "q", "k")
    for key in ("", " padded", "x" * 201):
        with pytest.raises(InvalidAdmission):
            idempotency_scope("a", "p", key)


def test_discovery_key_orders_by_creation_then_identity() -> None:
    early, _ = admitted(operation_id=UUID(int=9), at=T0)
    late, _ = admitted(operation_id=UUID(int=1), at=T0 + timedelta(microseconds=1))
    assert discovery_key(early) < discovery_key(late)
    with pytest.raises(InvalidAdmission):
        discovery_key(admitted(at=datetime(1969, 1, 1, tzinfo=UTC))[0])


def test_discovery_request_is_bounded() -> None:
    for limit in (0, 101, "5"):
        with pytest.raises(ValidationError):
            DiscoveryRequest(limit=limit)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        DiscoveryRequest.model_validate({"tenant_id": "other"})
    with pytest.raises(ValidationError):
        DiscoveryRequest(after="not-a-key")


# --- Admission service (repository fake) --------------------------------------------


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[OperationRecord, AdmissionRecord]] = []

    async def admit(
        self, operation: OperationRecord, admission: AdmissionRecord
    ) -> tuple[ManagedOperation, bool]:
        self.calls.append((operation, admission))
        record = ManagedOperation(
            schema_version=3,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
            admission=admission,
        )
        return record, True

    async def discover(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError


def service(repository: Any, **kwargs: Any) -> OperationAdmissionService:
    return OperationAdmissionService(
        repository=repository, catalog=Catalog(WEATHER), clock=lambda: T0, **kwargs
    )


@pytest.mark.asyncio
async def test_service_builds_descriptor_from_trusted_provenance_only() -> None:
    repository = RecordingRepository()
    result = await service(repository).admit(request(), authorization=provenance())
    operation, admission = repository.calls[0]
    assert result.created and not result.notified
    assert operation.state == OperationState.PENDING and operation.kind == WEATHER.signal
    assert admission.descriptor.capability == WEATHER
    assert (admission.descriptor.tenant_id, admission.descriptor.requester_id) == (
        "tenant-a",
        "agent.adam",
    )
    with pytest.raises(ValidationError):
        request(tenant_id="tenant-b")  # Identity is never accepted from the request.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"version": "2.0.0"},
        {"signal": "weather.unknown"},
    ],
)
async def test_service_rejects_unregistered_capability_versions(overrides: Any) -> None:
    repository = RecordingRepository()
    with pytest.raises(UnknownCapability):
        await service(repository).admit(request(**overrides), authorization=provenance())
    assert not repository.calls


@pytest.mark.asyncio
async def test_service_never_substitutes_a_different_resolved_version() -> None:
    class LatestCatalog:
        def resolve(self, signal: str, version: str) -> CapabilityRef:
            return WEATHER.model_copy(update={"version": "9.9.9"})

    with pytest.raises(UnknownCapability):
        await OperationAdmissionService(
            repository=RecordingRepository(), catalog=LatestCatalog()
        ).admit(request(), authorization=provenance())


@pytest.mark.asyncio
async def test_service_rejects_unapproved_recovery_policy() -> None:
    policy = RecoveryPolicyRef(name="ops.recovery.auto_replay", version=1)
    with pytest.raises(UnapprovedRecoveryPolicy):
        await service(RecordingRepository()).admit(
            request(recovery_policy=policy), authorization=provenance()
        )


@pytest.mark.asyncio
async def test_notification_failure_never_fails_durable_admission() -> None:
    notifier = Notifier(RuntimeError("broker unavailable"))
    result = await service(RecordingRepository(), notifier=notifier).admit(
        request(), authorization=provenance()
    )
    assert result.created and not result.notified and len(notifier.notices) == 1


class Messaging:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish_event(self, exchange_name: str, routing_key: str, message: Any, **kw: Any):
        self.published.append({"routing_key": routing_key, "message": message, **kw})

    async def setup_event_queue(self, *args: Any) -> None: ...

    async def subscribe(self, *args: Any) -> str:
        return "registration"

    async def stop_consuming(self, *args: Any) -> None: ...


def runtime(messaging: Messaging, grants: dict[str, EventGrants]) -> CognitiveEventRuntime:
    registry = CognitiveEventRegistry()
    register_dispatch_contract(registry)
    return CognitiveEventRuntime(
        messaging, registry, CognitiveEventPolicy(grants), signing_key=b"k" * 32
    )


@pytest.mark.asyncio
async def test_dispatch_notice_is_signed_reference_only_event_v2() -> None:
    messaging = Messaging()
    granted = {"system.enlil": EventGrants(publish=frozenset({DISPATCH_EVENT_TYPE}))}
    notifier = CognitiveEventDispatchNotifier(
        runtime(messaging, granted), principal_id="system.enlil"
    )
    record = aggregate()
    await notifier.notify(OperationDispatchNotice.for_operation(record))
    (sent,) = messaging.published
    envelope = sent["message"]
    assert sent["routing_key"] == DISPATCH_EVENT_TYPE and "caos-signature" in sent["headers"]
    assert envelope["tenant_id"] == "tenant-a" and envelope["actor_id"] == "system.enlil"
    assert envelope["correlation_id"] == "corr-1"
    assert envelope["causation_id"] == str(record.snapshot.operation_id)
    assert set(envelope["payload"]) == set(OperationDispatchNotice.model_fields)
    assert "Lisbon" not in json.dumps(envelope)  # No executable payload or input.

    denied = CognitiveEventDispatchNotifier(runtime(Messaging(), {}), principal_id="system.enlil")
    with pytest.raises(EventPermissionError):
        await denied.notify(OperationDispatchNotice.for_operation(record))


# --- Real MongoDB --------------------------------------------------------------------


@pytest_asyncio.fixture
async def mongo() -> AsyncIterator[tuple[Any, str]]:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo admission tests")
    from src.base.dependencies.containers.db_container import DatabaseContainer

    database = DatabaseContainer()
    infrastructure = database.mongodb_repository()
    name = f"ops4c_test_{uuid4().hex}"
    collection = await infrastructure.get_collection(name)
    try:
        yield infrastructure, name
    finally:
        try:
            await collection.drop()
        finally:
            await database.mongo_client().disconnect()


async def raw(mongo: tuple[Any, str]) -> Any:
    infrastructure, name = mongo
    return await infrastructure.get_collection(name)


def adapters(mongo: tuple[Any, str]) -> tuple[MongoOperationAdmissionRepository, ...]:
    return tuple(MongoOperationAdmissionRepository(*mongo) for _ in range(2))


@pytest.mark.asyncio
async def test_admission_is_one_atomic_document_restored_by_fresh_adapters(mongo: Any) -> None:
    first, second = adapters(mongo)
    operation, admission = admitted()
    record, created = await first.admit(operation, admission)
    assert created
    documents = await (await raw(mongo)).find({}).to_list(length=10)
    assert len(documents) == 1
    document = documents[0]
    assert document["schema_version"] == 3 and "revision" not in document
    assert document["snapshot"]["state"] == "pending"
    assert document["execution"]["status"] == "idle"
    restored = await MongoManagedOperationRepository(*mongo).inspect(operation.operation_id)
    assert restored == record
    assert restored.admission is not None
    assert restored.admission.descriptor.digest == admission.descriptor.digest
    replay, created = await second.admit(*admitted(key="k1"))
    assert not created and replay == record


@pytest.mark.asyncio
async def test_scoped_idempotency_conflicts_and_independent_scopes(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    original, _ = await repository.admit(*admitted())
    with pytest.raises(IdempotencyConflict):
        await repository.admit(*admitted(input=InlineInput.from_value({"city": "Porto"})))
    with pytest.raises(IdempotencyConflict):
        await repository.admit(*admitted(capability=WEATHER.model_copy(update={"version": "1.1"})))
    # Lineage/evidence differences do not make a retried request a different request.
    retried, created = await repository.admit(*admitted(correlation_id="corr-retry"))
    assert not created and retried == original
    other_requester, created = await repository.admit(
        *admitted(authorization=provenance(principal="agent.other"))
    )
    other_tenant, created_tenant = await repository.admit(
        *admitted(authorization=provenance(tenant="tenant-b"))
    )
    assert created and created_tenant
    ids = {r.snapshot.operation_id for r in (original, other_requester, other_tenant)}
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_concurrent_same_key_admissions_create_exactly_one(mongo: Any) -> None:
    repositories = [MongoOperationAdmissionRepository(*mongo) for _ in range(8)]
    await repositories[0].ensure_indexes()
    results = await asyncio.gather(*(r.admit(*admitted()) for r in repositories))
    assert sum(created for _, created in results) == 1
    assert len({record.snapshot.operation_id for record, _ in results}) == 1
    assert await (await raw(mongo)).count_documents({}) == 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_admissions_do_not_both_win(mongo: Any) -> None:
    repository, other = adapters(mongo)
    await repository.ensure_indexes()
    results = await asyncio.gather(
        repository.admit(*admitted()),
        other.admit(*admitted(input=InlineInput.from_value({"city": "Porto"}))),
        return_exceptions=True,
    )
    assert sum(isinstance(r, IdempotencyConflict) for r in results) == 1
    assert await (await raw(mongo)).count_documents({}) == 1


@pytest.mark.asyncio
async def test_ambiguous_admission_is_resolved_by_same_key_replay(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    await repository.ensure_indexes()
    collection = await repository._collection()

    class LostResponse:
        def __getattr__(self, name: str) -> Any:
            return getattr(collection, name)

        async def insert_one(self, document: dict[str, Any]) -> None:
            await collection.insert_one(document)
            raise AutoReconnect("response lost after commit")

    async def lossy() -> Any:
        return LostResponse()

    repository._collection = lossy  # type: ignore[method-assign]
    operation, admission = admitted()
    with pytest.raises(AmbiguousManagedWrite):
        await repository.admit(operation, admission)
    replay, created = await MongoOperationAdmissionRepository(*mongo).admit(*admitted())
    assert not created and replay.snapshot.operation_id == operation.operation_id


@pytest.mark.asyncio
async def test_legacy_writers_cannot_read_or_mutate_admitted_records(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    record, _ = await repository.admit(*admitted())
    operation_id = record.snapshot.operation_id
    legacy = MongoOperationRepository(*mongo)
    with pytest.raises(CorruptSnapshot):
        await legacy.get(operation_id)
    started = start_attempt(record.snapshot, at=T0 + timedelta(seconds=1), attempt_id=uuid4())
    with pytest.raises(CorruptSnapshot):
        await legacy.commit(started, expected_revision=0)
    collection = await raw(mongo)
    # A pre-04C.1 managed binary filters on exact schema_version=2 and cannot match.
    assert (
        await collection.find_one_and_update(
            {"_id": str(operation_id), "schema_version": 2}, {"$set": {"x": 1}}
        )
        is None
    )
    # A pre-upgrade OPS2-02 writer filters on top-level revision, which never exists.
    result = await collection.replace_one({"_id": str(operation_id), "revision": 0}, {"x": 1})
    assert result.matched_count == 0
    assert await MongoManagedOperationRepository(*mongo).inspect(operation_id) == record


@pytest.mark.asyncio
async def test_v2_managed_records_are_unchanged_and_not_discoverable(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    managed = MongoManagedOperationRepository(*mongo)
    v2 = await managed.create(OperationRecord.create(kind="legacy", producer="test", at=T0))
    await MongoOperationRepository(*mongo).create(
        OperationRecord.create(kind="legacy", producer="test", at=T0)
    )
    await repository.ensure_indexes()
    document = await (await raw(mongo)).find_one({"_id": str(v2.snapshot.operation_id)})
    assert document["schema_version"] == 2 and "admission" not in document
    assert (await repository.discover(DiscoveryRequest())).candidates == ()


@pytest.mark.asyncio
async def test_duplicate_discovery_is_safe_one_fenced_activation_wins(mongo: Any) -> None:
    repository, other = adapters(mongo)
    record, _ = await repository.admit(*admitted())
    pages = await asyncio.gather(
        repository.discover(DiscoveryRequest()), other.discover(DiscoveryRequest())
    )
    (first,), (second,) = (page.candidates for page in pages)
    assert first == second and first.operation_id == record.snapshot.operation_id

    managers = [MongoManagedOperationRepository(*mongo) for _ in range(2)]

    async def activate(manager: MongoManagedOperationRepository, owner: str) -> Any:
        return await manager.activate(
            first.operation_id,
            expected_revision=first.expected_revision,
            expected_generation=first.expected_generation,
            attempt_id=uuid4(),
            owner_id=owner,
            lease_ms=30_000,
            at=T0 + timedelta(seconds=1),
            command_id=uuid4(),
        )

    results = await asyncio.gather(
        activate(managers[0], "worker-a"), activate(managers[1], "worker-b"), return_exceptions=True
    )
    winners = [r for r in results if isinstance(r, ManagedOperation)]
    assert len(winners) == 1
    (loser,) = [r for r in results if r not in winners]
    assert isinstance(loser, (RevisionConflict, OwnershipConflict, ObsoleteFencingToken)), loser
    (winner,) = winners
    assert winner.schema_version == 3 and winner.admission == record.admission
    assert winner.execution.status == EpochStatus.ACTIVE
    # Leaving pending removes it from discovery; admission data is untouched by fencing.
    assert (await repository.discover(DiscoveryRequest())).candidates == ()


async def explain(
    repository: MongoOperationAdmissionRepository, query: dict[str, Any], index: str, limit: int
) -> dict[str, Any]:
    collection = await repository._collection()
    return (
        await collection.find(query)
        .sort("admission.discovery_key", 1)
        .hint(index)
        .limit(limit)
        .explain()
    )


def stages(plan: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(plan, dict):
        if "stage" in plan:
            found.append(plan)
        for value in plan.values():
            found.extend(stages(value))
    elif isinstance(plan, list):
        for value in plan:
            found.extend(stages(value))
    return found


@pytest.mark.asyncio
async def test_discovery_is_bounded_ordered_paginated_and_indexed(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    expected = []
    for i in range(7):
        record, _ = await repository.admit(*admitted(at=T0 + timedelta(seconds=i), key=f"key-{i}"))
        expected.append(record.snapshot.operation_id)
    # Noise that must stay outside the partial indexes.
    managed = MongoManagedOperationRepository(*mongo)
    for i in range(20):
        await managed.create(OperationRecord.create(kind="noise", producer="test", at=T0))
    seen, after = [], None
    while True:
        page = await repository.discover(DiscoveryRequest(limit=3, after=after))
        assert len(page.candidates) <= 3
        seen.extend(c.operation_id for c in page.candidates)
        if page.next_after is None:
            break  # Short page: the sweep is complete.
        after = page.next_after
    assert seen == expected

    plan = await explain(
        repository,
        {"schema_version": 3, "snapshot.state": "pending"},
        PENDING_INDEX,
        3,
    )
    found = stages(plan["queryPlanner"]["winningPlan"])
    assert not any(stage["stage"] == "COLLSCAN" for stage in found)
    assert any(stage.get("indexName") == PENDING_INDEX for stage in found)
    statistics = plan["executionStats"]
    assert statistics["totalDocsExamined"] <= 3 and statistics["totalKeysExamined"] <= 4


@pytest.mark.asyncio
async def test_tenant_discovery_is_isolated_and_indexed(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    for i in range(5):
        await repository.admit(*admitted(authorization=provenance(tenant="tenant-b"), key=f"b-{i}"))
    mine, _ = await repository.admit(*admitted(key="a"))
    page = await repository.discover(DiscoveryRequest(limit=10), tenant_id="tenant-a")
    assert [c.operation_id for c in page.candidates] == [mine.snapshot.operation_id]
    assert all(
        c.tenant_id == "tenant-b"
        for c in (await repository.discover(DiscoveryRequest(), tenant_id="tenant-b")).candidates
    )
    plan = await explain(
        repository,
        {
            "schema_version": 3,
            "snapshot.state": "pending",
            "admission.descriptor.tenant_id": "tenant-a",
        },
        TENANT_PENDING_INDEX,
        10,
    )
    assert plan["executionStats"]["totalDocsExamined"] == 1
    assert not any(s["stage"] == "COLLSCAN" for s in stages(plan["queryPlanner"]["winningPlan"]))


@pytest.mark.asyncio
async def test_discovery_without_indexes_fails_closed(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    await (await raw(mongo)).insert_one({"_id": "placeholder"})
    with pytest.raises(OperationFailure):
        await repository.discover(DiscoveryRequest())


@pytest.mark.asyncio
async def test_missed_publication_is_recovered_by_discovery_after_restart(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    notifier = Notifier(RuntimeError("publisher confirm timed out"))
    result = await OperationAdmissionService(
        repository=repository, catalog=Catalog(WEATHER), notifier=notifier
    ).admit(request(), authorization=provenance())
    assert result.created and not result.notified
    # A new process with no session, notification state or in-memory hint.
    restarted = MongoOperationAdmissionRepository(*mongo)
    page = await restarted.discover(DiscoveryRequest())
    assert [c.operation_id for c in page.candidates] == [result.operation.snapshot.operation_id]
    # Duplicate notification on replay is expected and harmless.
    replay = await OperationAdmissionService(
        repository=restarted, catalog=Catalog(WEATHER), notifier=Notifier()
    ).admit(request(), authorization=provenance())
    assert not replay.created and replay.notified
    assert replay.operation.snapshot.operation_id == result.operation.snapshot.operation_id


@pytest.mark.asyncio
async def test_existing_indexes_with_conflicting_options_fail_closed(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    await (await raw(mongo)).create_index(
        "admission.idempotency_scope", name="ops_v2_admission_idempotency"
    )
    with pytest.raises(OperationFailure):
        await repository.admit(*admitted())


# --- OPS2-04C.1 acceptance-audit regressions ----------------------------------------


def test_v2_model_serialization_keeps_ops2_04b_shape() -> None:
    operation = OperationRecord.create(kind="test", producer="test", at=T0)
    v2 = ManagedOperation(
        schema_version=2,
        snapshot=operation,
        execution=ExecutionEpoch.idle(),
        last_state_change=None,
    )
    assert (
        set(v2.model_dump())
        == set(v2.model_dump(mode="json"))
        == {
            "schema_version",
            "snapshot",
            "execution",
            "last_state_change",
        }
    )
    assert ManagedOperation.model_validate_json(v2.model_dump_json()) == v2
    v3 = aggregate()
    assert ManagedOperation.model_validate_json(v3.model_dump_json()) == v3


def test_request_digest_ignores_resolved_executable_but_not_requested_version() -> None:
    from src.base.operations.v2.admission import request_digest

    moved = WEATHER.model_copy(update={"provider": "tool.weather2", "executable": "m:moved"})
    assert request_digest(descriptor()) == request_digest(descriptor(capability=moved))
    bumped = WEATHER.model_copy(update={"version": "1.0.1"})
    assert request_digest(descriptor()) != request_digest(descriptor(capability=bumped))


REFERENCE = InputReference(
    kind="reference", uri="caos-blob://inputs/forecast.json", digest="a" * 64, size_bytes=12
)


@pytest.mark.asyncio
async def test_input_references_fail_closed_without_an_approved_store() -> None:
    repository = RecordingRepository()
    with pytest.raises(InvalidAdmission):
        await service(repository).admit(request(input=REFERENCE), authorization=provenance())
    assert not repository.calls


@pytest.mark.asyncio
async def test_input_references_require_tenant_scoped_store_verification() -> None:
    class Verifier:
        def __init__(self, error: Exception | None = None) -> None:
            self.error, self.calls = error, []

        async def verify(self, reference: InputReference, *, tenant_id: str) -> None:
            self.calls.append((reference, tenant_id))
            if self.error:
                raise self.error

    rejecting, repository = Verifier(InvalidAdmission("digest mismatch")), RecordingRepository()
    with pytest.raises(InvalidAdmission):
        await service(repository, references=rejecting).admit(
            request(input=REFERENCE), authorization=provenance()
        )
    assert not repository.calls and rejecting.calls == [(REFERENCE, "tenant-a")]
    accepting = Verifier()
    result = await service(repository, references=accepting).admit(
        request(input=REFERENCE), authorization=provenance(tenant="tenant-b")
    )
    assert accepting.calls == [(REFERENCE, "tenant-b")]
    assert result.operation.admission is not None
    assert result.operation.admission.descriptor.input == REFERENCE


@pytest.mark.asyncio
async def test_every_fenced_mutation_preserves_admission(mongo: Any) -> None:
    """activate/renew/release/takeover/commit_outcome on schema 3 never touch admission."""
    from src.base.operations.v2.models import AttemptState
    from src.base.operations.v2.transitions import finish_attempt

    admissions, _ = adapters(mongo)
    manager = MongoManagedOperationRepository(*mongo)
    collection = await raw(mongo)

    async def unchanged(original: ManagedOperation) -> ManagedOperation:
        current = await manager.inspect(original.snapshot.operation_id)
        stored = await collection.find_one({"_id": str(original.snapshot.operation_id)})
        assert current.schema_version == stored["schema_version"] == 3
        assert current.admission == original.admission
        assert stored["admission"] == managed_to_document(original)["admission"]
        return current

    # Unresolved path: activate -> renew -> release -> reconcile takeover -> renew -> release.
    first, _ = await admissions.admit(*admitted(key="unresolved"))
    op = first.snapshot.operation_id
    attempt = uuid4()
    step = await manager.activate(
        op,
        expected_revision=0,
        expected_generation=0,
        attempt_id=attempt,
        owner_id="a",
        lease_ms=30_000,
        at=T0 + timedelta(seconds=1),
        command_id=uuid4(),
    )
    await unchanged(first)
    await manager.renew(op, attempt_id=attempt, owner_id="a", generation=1, lease_ms=30_000)
    await unchanged(first)
    await manager.release(op, attempt_id=attempt, owner_id="a", generation=1)
    await unchanged(first)
    await manager.takeover(
        op,
        expected_revision=step.snapshot.revision,
        expected_generation=1,
        attempt_id=attempt,
        owner_id="b",
        lease_ms=30_000,
    )
    await manager.renew(op, attempt_id=attempt, owner_id="b", generation=2, lease_ms=30_000)
    await manager.release(op, attempt_id=attempt, owner_id="b", generation=2)
    await unchanged(first)

    # Outcome path: activate -> failed with retry -> reactivate -> terminal success.
    second, _ = await admissions.admit(*admitted(key="outcome"))
    op = second.snapshot.operation_id
    current = second
    for number, (outcome, retry) in enumerate(
        ((AttemptState.FAILED, True), (AttemptState.SUCCEEDED, False))
    ):
        attempt = uuid4()
        current = await manager.activate(
            op,
            expected_revision=current.snapshot.revision,
            expected_generation=current.execution.generation,
            attempt_id=attempt,
            owner_id=f"w{number}",
            lease_ms=30_000,
            at=T0 + timedelta(seconds=2 * number + 1),
            command_id=uuid4(),
        )
        finished = finish_attempt(
            current.snapshot,
            attempt_id=attempt,
            outcome=outcome,
            reason=None if outcome == AttemptState.SUCCEEDED else "transient",
            retry=retry,
            at=T0 + timedelta(seconds=2 * number + 2),
        )
        await manager.commit_outcome(
            finished,
            expected_revision=current.snapshot.revision,
            attempt_id=attempt,
            owner_id=f"w{number}",
            generation=current.execution.generation,
            command_id=uuid4(),
        )
        current = await unchanged(second)
    assert current.snapshot.state == OperationState.SUCCEEDED
    assert current.execution.status == EpochStatus.CLOSED
    # Neither unresolved nor retry-pending/terminal work is rediscovered as new admission.
    assert (await admissions.discover(DiscoveryRequest())).candidates == ()


@pytest.mark.asyncio
async def test_representative_discovery_plans_are_bounded_and_isolated(mongo: Any) -> None:
    from src.base.operations.v2.mongo_repository import snapshot_to_document

    repository, _ = adapters(mongo)
    await repository.ensure_indexes()
    collection = await raw(mongo)
    tenants = ("tenant-a", "tenant-b", "tenant-c")
    records = [
        aggregate(
            authorization=provenance(tenant=tenants[i % 3]),
            at=T0 + timedelta(milliseconds=i),
            key=f"bulk-{i}",
        )
        for i in range(600)
    ]
    await collection.insert_many([managed_to_document(r) for r in records])
    # Representative noise outside the partial indexes: v1, v2 and activated v3 records.
    await collection.insert_many(
        [
            snapshot_to_document(OperationRecord.create(kind="v1", producer="t", at=T0))
            for _ in range(200)
        ]
        + [
            managed_to_document(
                ManagedOperation(
                    schema_version=2,
                    snapshot=OperationRecord.create(kind="v2", producer="t", at=T0),
                    execution=ExecutionEpoch.idle(),
                    last_state_change=None,
                )
            )
            for _ in range(200)
        ]
    )
    manager = MongoManagedOperationRepository(*mongo)
    activated = set()
    for record in records[:60]:
        await manager.activate(
            record.snapshot.operation_id,
            expected_revision=0,
            expected_generation=0,
            attempt_id=uuid4(),
            owner_id="w",
            lease_ms=30_000,
            at=T0 + timedelta(seconds=5),
            command_id=uuid4(),
        )
        activated.add(record.snapshot.operation_id)
    pending = [r for r in records if r.snapshot.operation_id not in activated]

    async def sweep(tenant: str | None) -> list[UUID]:
        seen, after = [], None
        while True:
            page = await repository.discover(
                DiscoveryRequest(limit=50, after=after), tenant_id=tenant
            )
            assert len(page.candidates) <= 50
            assert tenant is None or {c.tenant_id for c in page.candidates} <= {tenant}
            seen.extend(c.operation_id for c in page.candidates)
            if page.next_after is None:
                return seen
            after = page.next_after

    assert await sweep(None) == [r.snapshot.operation_id for r in pending]
    for tenant in tenants:
        mine = [
            r.snapshot.operation_id for r in pending if r.admission.descriptor.tenant_id == tenant
        ]
        result = await sweep(tenant)
        assert result == mine and len(set(result)) == len(result)

    # Explain the exact production cursor mid-sweep for both index paths.
    middle = pending[len(pending) // 2].admission.discovery_key
    for tenant, index in ((None, PENDING_INDEX), ("tenant-b", TENANT_PENDING_INDEX)):
        cursor = await repository._cursor(DiscoveryRequest(limit=25, after=middle), tenant)
        plan = await cursor.explain()
        found = stages(plan["queryPlanner"]["winningPlan"])
        names = {stage["stage"] for stage in found}
        assert "COLLSCAN" not in names and "SORT" not in names, names
        assert {stage.get("indexName") for stage in found if stage["stage"] == "IXSCAN"} == {index}
        statistics = plan["executionStats"]
        assert statistics["nReturned"] == 25
        assert statistics["totalDocsExamined"] == 25
        assert statistics["totalKeysExamined"] <= 26


@pytest.mark.asyncio
async def test_work_admitted_behind_a_cursor_is_reached_on_the_next_sweep(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    for i in range(3):
        await repository.admit(*admitted(at=T0 + timedelta(seconds=10 + i), key=f"k{i}"))
    page = await repository.discover(DiscoveryRequest(limit=2))
    assert page.next_after is not None
    # Another instance with a slower clock admits work older than the current cursor.
    late, _ = await repository.admit(*admitted(at=T0, key="skewed"))
    rest = await repository.discover(DiscoveryRequest(limit=2, after=page.next_after))
    assert late.snapshot.operation_id not in {c.operation_id for c in rest.candidates}
    assert rest.next_after is None  # Short page ends the sweep; the next one restarts.
    restart = await repository.discover(DiscoveryRequest(limit=2, after=rest.next_after))
    assert restart.candidates[0].operation_id == late.snapshot.operation_id


@pytest.mark.asyncio
async def test_ambiguous_admission_replay_survives_executable_redeploy(mongo: Any) -> None:
    repository, _ = adapters(mongo)
    original, _ = await repository.admit(*admitted())
    moved = WEATHER.model_copy(update={"executable": "domains.weather.v2:fetch"})
    replay, created = await repository.admit(*admitted(capability=moved))
    assert not created and replay == original
    assert replay.admission is not None
    assert replay.admission.descriptor.capability == WEATHER  # Original binding retained.
