"""CAOS audit log foundation: contract, append-only persistence and trusted service."""

from __future__ import annotations

import copy
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from pymongo.errors import AutoReconnect, DuplicateKeyError, OperationFailure

from src.domains.agentverse.audit.models import (
    EVENT_CATALOG,
    LOGS_COLLECTION,
    CAOSLogRecord,
    LogEvent,
    stable_event_id,
    storage_id,
)
from src.domains.agentverse.audit.repository import (
    AmbiguousLogWrite,
    CorruptLogRecord,
    LogConflict,
    LogUnavailable,
    MongoLogRepository,
)
from src.domains.agentverse.audit.service import CAOSLogService
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.services.db_service import DBService

TENANT, OTHER_TENANT = "caos-local", "tenant-b"
OPERATOR, GENESIS, ENKI = "operator-1", "genesis-principal", "enki-agent"
CORRELATION = "corr-genesis-1"
T0 = datetime(2026, 10, 29, 12, 0, 0, 123456, tzinfo=UTC)


def provenance(tenant: str = TENANT, principal: str = GENESIS, user: str = OPERATOR):
    permit = ExecutionPermit(
        AgentInstanceKey(tenant, user, principal, "secret-session"),
        ("incarnation:policy-v1", "inc", "0", "0"),
    )
    return permit_provenance(permit, "store.create.provisioned")


def store_event(**override: Any) -> LogEvent:
    value: dict[str, Any] = {
        "event_id": stable_event_id(
            correlation_id=CORRELATION, event_type="genesis.store.completed", step="enki"
        ),
        "event_type": "genesis.store.completed",
        "outcome": "succeeded",
        "subject": {"resource_type": "agent", "resource_id": ENKI},
        "target": {"signal": "store.create.provisioned", "version": "1.0.0"},
        "correlation": {
            "correlation_id": CORRELATION,
            "causation_id": "synapse-7",
            "operation_id": "6f1c6a51-2d0b-4f3c-9a50-2a3c2f1c0b11",
        },
        "metadata": {
            "resource_type": "agent",
            "created": True,
            "request_digest": "a" * 64,
            "slot": "enki",
        },
    }
    value.update(override)
    return LogEvent.model_validate(value)


# --- Fake collection --------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def sort(self, field: str, direction: int) -> FakeCursor:
        self.rows = sorted(self.rows, key=lambda r: r[field], reverse=direction < 0)
        return self

    def limit(self, n: int) -> FakeCursor:
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        return [copy.deepcopy(r) for r in self.rows[:length]]


class FakeCollection:
    """Built-in unique _id like MongoDB; records every operation; failure hooks."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.indexes: dict[str, Any] = {}
        self.write_concern = SimpleNamespace(acknowledged=True)
        self.insert_error: Exception | None = None
        self.commit_then_fail = False
        self.hide_reads = False
        self.read_error: Exception | None = None
        self.index_error: Exception | None = None

    def with_options(self, **_: Any) -> FakeCollection:
        return self

    async def create_index(self, keys: Any, **options: Any) -> str:
        self.calls.append("create_index")
        if self.index_error is not None:
            raise self.index_error
        self.indexes[options["name"]] = {"keys": keys, **options}
        return options["name"]

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.calls.append("insert_one")
        if self.insert_error is not None:
            raise self.insert_error
        if document["_id"] in self.documents:
            raise DuplicateKeyError("dup", 11000, {"keyPattern": {"_id": 1}})
        self.documents[document["_id"]] = copy.deepcopy(document)
        if self.commit_then_fail:
            raise AutoReconnect("connection lost after commit")
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append("find_one")
        if self.read_error is not None:
            raise self.read_error
        if self.hide_reads:
            return None
        row = self.documents.get(query["_id"])
        if row is None or any(row.get(k) != v for k, v in query.items() if k != "_id"):
            return None
        return copy.deepcopy(row)

    def find(self, query: dict[str, Any]) -> FakeCursor:
        self.calls.append("find")
        return FakeCursor(
            [r for r in self.documents.values() if all(r.get(k) == v for k, v in query.items())]
        )

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"append-only log must not call {name}")


class FakeMongo:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.names: list[str] = []

    async def get_collection(self, name: str) -> FakeCollection:
        self.names.append(name)
        return self.collection


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        value = self.now
        self.now = self.now + timedelta(seconds=1)
        return value


def service(mongo: Any = None, clock: Clock | None = None) -> SimpleNamespace:
    mongo = mongo or FakeMongo()
    repository = MongoLogRepository(mongo)
    return SimpleNamespace(
        mongo=mongo,
        repository=repository,
        service=CAOSLogService(repository=repository, clock=clock or Clock()),
    )


# --- Valid Genesis records ---------------------------------------------------------------


GENESIS_EVENTS = {
    "genesis.initiation.authorized": (
        "succeeded",
        {"event_version": 1, "slot": "enki", "executor_id": GENESIS, "creation_digest": "a" * 64},
    ),
    "genesis.validation.completed": (
        "rejected",
        {
            "resource_type": "agent",
            "valid": False,
            "issue_codes": ["reference.unavailable", "field.unknown"],
        },
    ),
    "genesis.composition.completed": (
        "succeeded",
        {"composed": True, "validation_digest": "c" * 64},
    ),
    "genesis.store.completed": (
        "succeeded",
        {"resource_type": "agent", "created": False, "request_digest": "d" * 64, "slot": "enki"},
    ),
    "genesis.store.failed": (
        "failed",
        {"resource_type": "agent", "code": "STORE_AMBIGUOUS_WRITE", "retryable": True},
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", sorted(GENESIS_EVENTS))
async def test_valid_genesis_audit_records_are_persisted_and_restored(event_type: str) -> None:
    outcome, metadata = GENESIS_EVENTS[event_type]
    s = service()
    event = store_event(
        event_id=stable_event_id(correlation_id=CORRELATION, event_type=event_type, step="enki"),
        event_type=event_type,
        outcome=outcome,
        metadata=metadata,
    )
    result = await s.service.append(event, authorization=provenance())
    assert result.created is True
    record = result.record
    assert (record.schema_version, record.event_type, record.outcome) == (1, event_type, outcome)
    assert record.tenant_id == TENANT
    assert record.recorded_at == T0.replace(microsecond=123000)
    (document,) = s.mongo.collection.documents.values()
    assert document["_id"] == storage_id(TENANT, event.event_id)
    assert document["recorded_at"] == "2026-10-29T12:00:00.123Z"
    assert await s.repository.get(TENANT, event.event_id) == record
    assert s.mongo.names == [LOGS_COLLECTION] * len(s.mongo.names)


def test_catalog_covers_exactly_the_genesis_events() -> None:
    assert set(EVENT_CATALOG) == set(GENESIS_EVENTS)


def test_serialization_is_deterministic() -> None:
    first = CAOSLogRecord.build(store_event(), authorization=provenance(), recorded_at=T0)
    second = CAOSLogRecord.build(store_event(), authorization=provenance(), recorded_at=T0)
    assert first.to_document() == second.to_document()
    assert list(first.to_document()) == list(second.to_document())
    later = CAOSLogRecord.build(
        store_event(), authorization=provenance(), recorded_at=T0 + timedelta(hours=1)
    )
    # The content digest identifies the event independently of when it was recorded.
    assert later.content_digest == first.content_digest


def test_timestamps_are_normalized_to_utc_and_naive_times_rejected() -> None:
    plus_two = T0.astimezone(timezone(timedelta(hours=2)))
    record = CAOSLogRecord.build(store_event(), authorization=provenance(), recorded_at=plus_two)
    assert record.recorded_at.tzinfo == UTC and record.recorded_at == T0.replace(microsecond=123000)
    with pytest.raises(ValidationError):
        CAOSLogRecord.build(
            store_event(), authorization=provenance(), recorded_at=T0.replace(tzinfo=None)
        )


# --- Invalid and unknown fields ----------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": TENANT},
        {"actor": {"principal_id": "x", "user_id": "y"}},
        {"authorization": {"authority": "utu"}},
        {"recorded_at": "2020-01-01T00:00:00Z"},
        {"schema_version": 1},
        {"content_digest": "e" * 64},
        {"payload": {"prompt": "secret"}},
    ],
)
def test_producers_cannot_supply_identity_time_authorization_or_payloads(
    override: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        store_event(**override)


@pytest.mark.parametrize(
    "override",
    [
        {"event_type": "genesis.unknown"},
        {"event_type": "agent.deleted"},
        {"outcome": "failed"},  # store.completed only succeeds
        {"outcome": "maybe"},
        {"event_id": "has spaces"},
        {"event_id": ""},
        {"subject": {"resource_type": "workflow", "resource_id": "x"}},
        {"subject": {"resource_type": "agent", "resource_id": "x", "tenant_id": "t"}},
        {"target": {"signal": "Store Create", "version": "1.0.0"}},
        {"correlation": {"correlation_id": " padded "}},
        {"correlation": {"correlation_id": "c", "operation_id": "not-a-uuid"}},
    ],
)
def test_invalid_events_are_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        store_event(**override)


# --- Metadata allowlists -----------------------------------------------------------------


@pytest.mark.parametrize(
    "metadata",
    [
        {"resource_type": "agent", "created": True, "request_digest": "a" * 64, "prompt": "x"},
        {"resource_type": "agent", "created": True, "request_digest": "a" * 64, "token": "jwt"},
        {"resource_type": "agent", "created": "yes", "request_digest": "a" * 64},
        {"resource_type": "agent", "created": True, "request_digest": "not-a-digest"},
        {"resource_type": "agent", "created": True, "request_digest": "a" * 64, "slot": "Bad Slot"},
        {"resource_type": "agent", "created": True},
        {"resource_type": "agent", "created": True, "request_digest": "a" * 64, "nested": {}},
    ],
)
def test_metadata_outside_the_event_allowlist_is_rejected(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        store_event(metadata=metadata)


@pytest.mark.parametrize(
    "codes",
    [["ok", "has space"], ["line\nbreak"], ["x" * 65], ["ok"] * 65, [{"code": "x"}]],
)
def test_issue_codes_are_bounded_identifiers(codes: list[Any]) -> None:
    with pytest.raises(ValidationError):
        store_event(
            event_type="genesis.validation.completed",
            outcome="rejected",
            metadata={"resource_type": "agent", "valid": False, "issue_codes": codes},
        )


def test_metadata_is_canonicalized_through_its_allowlist_model() -> None:
    event = store_event(
        event_type="genesis.composition.completed",
        outcome="rejected",
        metadata={"composed": False, "issue_codes": ("validation.digest_mismatch",)},
    )
    assert event.metadata == {
        "composed": False,
        "issue_codes": ["validation.digest_mismatch"],
        "validation_digest": None,
    }


# --- Attribution -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_records_attribute_the_initiating_principal_and_authorizing_user() -> None:
    s = service()
    result = await s.service.append(store_event(), authorization=provenance())
    record = result.record
    assert (record.actor.principal_id, record.actor.user_id) == (GENESIS, OPERATOR)
    assert record.authorization == provenance()
    assert record.authorization.authority == "utu"
    stored = repr(next(iter(s.mongo.collection.documents.values())))
    assert "secret-session" not in stored  # permit provenance never carries the session


@pytest.mark.asyncio
async def test_unverified_attribution_is_refused() -> None:
    s = service()
    with pytest.raises(TypeError):
        await s.service.append(store_event(), authorization=provenance().model_dump())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await s.service.append(store_event().model_dump(), authorization=provenance())  # type: ignore[arg-type]
    assert s.mongo.collection.calls == []


def test_record_identity_must_match_its_provenance() -> None:
    document = CAOSLogRecord.build(
        store_event(), authorization=provenance(), recorded_at=T0
    ).model_dump(mode="json")
    for field, value in (
        ("tenant_id", OTHER_TENANT),
        ("actor", {"principal_id": "someone", "user_id": OPERATOR}),
        ("actor", {"principal_id": GENESIS, "user_id": "someone"}),
        ("content_digest", "f" * 64),
        ("metadata", {**document["metadata"], "created": False}),
    ):
        with pytest.raises(ValidationError):
            CAOSLogRecord.model_validate({**document, field: value})


# --- Duplicates and append-only ------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_ingestion_returns_the_original_record_unchanged() -> None:
    s = service()
    first = await s.service.append(store_event(), authorization=provenance())
    again = await s.service.append(store_event(), authorization=provenance())  # later clock
    assert again.created is False
    assert again.record == first.record and again.record.recorded_at == first.record.recorded_at
    assert len(s.mongo.collection.documents) == 1


@pytest.mark.asyncio
async def test_same_event_id_with_different_content_conflicts_without_overwriting() -> None:
    s = service()
    first = await s.service.append(store_event(), authorization=provenance())
    altered = store_event(
        metadata={**first.record.metadata, "created": False},
    )
    with pytest.raises(LogConflict):
        await s.service.append(altered, authorization=provenance())
    with pytest.raises(LogConflict):  # same event, different attributed user
        await s.service.append(store_event(), authorization=provenance(user="someone-else"))
    (document,) = s.mongo.collection.documents.values()
    assert document == first.record.to_document()


def test_records_are_immutable_and_the_repository_has_no_mutation_api() -> None:
    record = CAOSLogRecord.build(store_event(), authorization=provenance(), recorded_at=T0)
    with pytest.raises(ValidationError):
        record.outcome = "failed"  # type: ignore[misc]
    for name in ("update", "replace", "delete", "upsert", "remove", "commit", "purge"):
        assert not any(name in attr for attr in dir(MongoLogRepository))


@pytest.mark.asyncio
async def test_append_path_only_inserts_and_reads() -> None:
    s = service()
    await s.service.append(store_event(), authorization=provenance())
    await s.service.append(store_event(), authorization=provenance())
    # FakeCollection raises on any update/replace/delete/TTL call.
    assert set(s.mongo.collection.calls) <= {"insert_one", "find_one"}


# --- Tenant isolation ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenants_are_isolated_even_for_equal_event_ids() -> None:
    s = service()
    a = await s.service.append(store_event(), authorization=provenance())
    b = await s.service.append(store_event(), authorization=provenance(tenant=OTHER_TENANT))
    assert a.created and b.created  # no cross-tenant dedupe, no conflict, no disclosure
    assert len(s.mongo.collection.documents) == 2
    event_id = store_event().event_id
    assert (await s.repository.get(TENANT, event_id)).tenant_id == TENANT
    assert (await s.repository.get(OTHER_TENANT, event_id)).tenant_id == OTHER_TENANT
    assert await s.repository.get("tenant-c", event_id) is None
    assert [r.tenant_id for r in await s.repository.list_by_correlation(TENANT, CORRELATION)] == [
        TENANT
    ]


@pytest.mark.asyncio
async def test_get_never_returns_a_record_whose_tenant_differs_from_the_query() -> None:
    s = service()
    await s.service.append(store_event(), authorization=provenance(tenant=OTHER_TENANT))
    event_id = store_event().event_id
    # Even a caller holding another tenant's storage ID cannot read across tenants.
    s.mongo.collection.documents[storage_id(TENANT, event_id)] = s.mongo.collection.documents.pop(
        storage_id(OTHER_TENANT, event_id)
    )
    assert await s.repository.get(TENANT, event_id) is None


# --- Correlation -----------------------------------------------------------------------------


def test_stable_event_ids_are_deterministic_and_step_specific() -> None:
    make = stable_event_id
    a = make(correlation_id=CORRELATION, event_type="genesis.store.completed", step="enki")
    assert a == make(correlation_id=CORRELATION, event_type="genesis.store.completed", step="enki")
    assert UUID(a)
    assert a != make(correlation_id=CORRELATION, event_type="genesis.store.failed", step="enki")
    assert a != make(correlation_id=CORRELATION, event_type="genesis.store.completed", step="adam")
    assert a != make(correlation_id="other", event_type="genesis.store.completed", step="enki")


@pytest.mark.asyncio
async def test_correlation_identifiers_are_preserved_and_listed_in_order() -> None:
    s = service()
    for event_type, (outcome, metadata) in GENESIS_EVENTS.items():
        await s.service.append(
            store_event(
                event_id=stable_event_id(
                    correlation_id=CORRELATION, event_type=event_type, step="enki"
                ),
                event_type=event_type,
                outcome=outcome,
                metadata=metadata,
            ),
            authorization=provenance(),
        )
    await s.service.append(
        store_event(event_id="unrelated", correlation={"correlation_id": "corr-other"}),
        authorization=provenance(),
    )
    records = await s.repository.list_by_correlation(TENANT, CORRELATION)
    assert [r.event_type for r in records] == list(GENESIS_EVENTS)
    assert {(r.correlation_id, r.causation_id, str(r.operation_id)) for r in records} == {
        (CORRELATION, "synapse-7", "6f1c6a51-2d0b-4f3c-9a50-2a3c2f1c0b11")
    }
    with pytest.raises(ValueError):
        await s.repository.list_by_correlation(TENANT, CORRELATION, limit=0)


# --- Persistence failures --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_insert_is_ambiguous_and_a_retry_records_once() -> None:
    s = service()
    s.mongo.collection.insert_error = AutoReconnect("down")
    with pytest.raises(AmbiguousLogWrite) as excinfo:
        await s.service.append(store_event(), authorization=provenance())
    assert excinfo.value.retryable is True
    s.mongo.collection.insert_error = None
    assert (await s.service.append(store_event(), authorization=provenance())).created
    assert len(s.mongo.collection.documents) == 1


@pytest.mark.asyncio
async def test_lost_acknowledgement_is_resolved_by_retrying_the_same_event() -> None:
    s = service()
    s.mongo.collection.commit_then_fail = True
    with pytest.raises(AmbiguousLogWrite):
        await s.service.append(store_event(), authorization=provenance())
    s.mongo.collection.commit_then_fail = False
    retry = await s.service.append(store_event(), authorization=provenance())
    assert retry.created is False and len(s.mongo.collection.documents) == 1


@pytest.mark.asyncio
async def test_duplicate_not_yet_visible_is_ambiguous() -> None:
    s = service()
    await s.service.append(store_event(), authorization=provenance())
    s.mongo.collection.hide_reads = True
    with pytest.raises(AmbiguousLogWrite):
        await s.service.append(store_event(), authorization=provenance())


@pytest.mark.asyncio
async def test_unacknowledged_writes_index_and_read_failures_fail_closed() -> None:
    s = service()
    s.mongo.collection.write_concern = SimpleNamespace(acknowledged=False)
    with pytest.raises(LogUnavailable):
        await s.service.append(store_event(), authorization=provenance())
    assert s.mongo.collection.documents == {}
    s = service()
    s.mongo.collection.index_error = OperationFailure("unsupported")
    with pytest.raises(LogUnavailable):
        await s.repository.ensure_indexes()
    s.mongo.collection.read_error = AutoReconnect("down")
    with pytest.raises(LogUnavailable):
        await s.repository.get(TENANT, "x")


@pytest.mark.asyncio
async def test_corrupt_stored_records_are_refused() -> None:
    s = service()
    await s.service.append(store_event(), authorization=provenance())
    key = storage_id(TENANT, store_event().event_id)
    for mutate in (
        lambda d: d.update(outcome="failed"),
        lambda d: d.update(extra="x"),
        lambda d: d.pop("content_digest"),
        lambda d: d["actor"].update(user_id="someone"),
    ):
        original = copy.deepcopy(s.mongo.collection.documents[key])
        mutate(s.mongo.collection.documents[key])
        with pytest.raises(CorruptLogRecord):
            await s.repository.get(TENANT, store_event().event_id)
        s.mongo.collection.documents[key] = original


@pytest.mark.asyncio
async def test_index_is_a_plain_compound_query_index() -> None:
    s = service()
    await s.repository.ensure_indexes()
    (spec,) = s.mongo.collection.indexes.values()
    assert spec["keys"] == [("tenant_id", 1), ("correlation_id", 1), ("recorded_at", 1)]
    assert not {"unique", "partialFilterExpression", "expireAfterSeconds"} & set(spec)


# --- Wiring, exposure and regression --------------------------------------------------------


def test_container_wires_the_log_service_over_the_shared_repository() -> None:
    from unittest.mock import Mock

    from dependency_injector import providers

    from src.domains.agentverse.dependencies.di_container import AgentverseContainer

    container = AgentverseContainer()
    shared = FakeMongo()
    container.mongodb_repository.override(providers.Object(shared))
    container.redis_repository.override(providers.Object(Mock()))
    service_ = container.caos_log_service()
    assert service_ is container.caos_log_service()
    assert service_._repository.repository is shared
    assert service_._repository.collection_name == "coll_logs"


def test_log_writing_is_not_exposed_to_agents() -> None:
    from src.domains.agentverse.registries import (
        synaptic_registry_instance,
        tool_registry_instance,
    )

    assert not any("log" in key for key in synaptic_registry_instance.list())
    assert not any(
        key in {"log", "logs", "audit", "caos_log"} for key in tool_registry_instance.list()
    )


def test_existing_operation_logging_is_untouched() -> None:
    from src.domains.agentverse.entities.report_log import OperationLogCreate, OperationType

    assert DBService.OPERATION_LOGS_COL == "coll_operation_logs" != LOGS_COLLECTION
    log = OperationLogCreate(
        operation_type=OperationType.AGENT_CREATE, user_object_id="u", payload={"a": 1}
    )
    assert log.model_dump() == {
        "user_object_id": "u",
        "operation_type": "agent_create",
        "payload": {"a": 1},
    }


# --- Real MongoDB --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mongo() -> AsyncIterator[tuple[Any, str]]:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo log tests")
    from src.base.dependencies.containers.db_container import DatabaseContainer

    database = DatabaseContainer()
    infrastructure = database.mongodb_repository()
    name = f"caos_log_test_{uuid4().hex}"
    collection = await infrastructure.get_collection(name)
    try:
        yield infrastructure, name
    finally:
        try:
            await collection.drop()
        finally:
            await database.mongo_client().disconnect()


@pytest.mark.asyncio
async def test_mongo_append_replay_conflict_isolation_and_order(mongo: Any) -> None:
    infrastructure, name = mongo
    repository = MongoLogRepository(infrastructure, name)
    log = CAOSLogService(repository=repository, clock=Clock())
    await repository.ensure_indexes()
    first = await log.append(store_event(), authorization=provenance())
    again = await log.append(store_event(), authorization=provenance())
    assert first.created and not again.created and again.record == first.record
    with pytest.raises(LogConflict):
        await log.append(
            store_event(metadata={**first.record.metadata, "created": False}),
            authorization=provenance(),
        )
    other = await log.append(store_event(), authorization=provenance(tenant=OTHER_TENANT))
    assert other.created
    await log.append(
        store_event(
            event_id="second",
            event_type="genesis.store.failed",
            outcome="failed",
            metadata={"resource_type": "agent", "code": "STORE_CONFLICT", "retryable": False},
        ),
        authorization=provenance(),
    )
    listed = await repository.list_by_correlation(TENANT, CORRELATION)
    assert [r.event_id for r in listed] == [first.record.event_id, "second"]
    assert await repository.get(TENANT, first.record.event_id) == first.record


# --- Scalability invariant: small, bounded records -------------------------------------------


def _max_validation_event(signal: str = "a." + "b" * 254) -> LogEvent:
    big = "x" * 256
    return LogEvent.model_validate(
        {
            "event_id": "e" * 128,
            "event_type": "genesis.validation.completed",
            "outcome": "rejected",
            "subject": {"resource_type": "agent", "resource_id": big},
            "target": {"signal": signal, "version": big},
            "correlation": {
                "correlation_id": big,
                "causation_id": big,
                "operation_id": "6f1c6a51-2d0b-4f3c-9a50-2a3c2f1c0b11",
            },
            "metadata": {
                "resource_type": "agent",
                "valid": False,
                "issue_codes": [f"c{i:02d}" + "z" * 61 for i in range(64)],
                "normalized_digest": "a" * 64,
            },
        }
    )


def test_largest_field_valid_record_fits_and_oversized_records_are_refused() -> None:
    from src.base.operations.v2.admission import AuthorizationProvenance, canonical_json
    from src.domains.agentverse.audit.models import MAX_RECORD_BYTES

    big = "x" * 256
    authorization = AuthorizationProvenance(
        authority="utu",
        tenant_id=big,
        principal_id=big,
        subject_id=big,
        policy_version=big,
        evidence_digest="b" * 64,
    )
    record = CAOSLogRecord.build(
        _max_validation_event(), authorization=authorization, recorded_at=T0
    )
    assert len(canonical_json(record.model_dump(mode="json")).encode()) <= MAX_RECORD_BYTES
    # Reused types without their own length bound (Signal) are capped by the record bound.
    with pytest.raises(ValidationError, match="size bound"):
        CAOSLogRecord.build(
            _max_validation_event(signal="a." + "b" * 20_000),
            authorization=authorization,
            recorded_at=T0,
        )
