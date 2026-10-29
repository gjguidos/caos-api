"""Shared adapter contract; real Mongo cases require explicit environment opt-in."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import BSON
from pymongo.errors import AutoReconnect

from src.base.operations.v2.memory_repository import InMemoryOperationRepository
from src.base.operations.v2.models import AttemptState, OperationRecord, OperationState
from src.base.operations.v2.mongo_repository import (
    MongoOperationRepository,
    snapshot_from_document,
    snapshot_to_document,
)
from src.base.operations.v2.repository import (
    CorruptSnapshot,
    DuplicateOperation,
    InvalidSnapshot,
    OperationNotFound,
    OperationRepository,
    RevisionConflict,
)
from src.base.operations.v2.tests.test_contracts import (
    FIRST,
    SECOND,
    T0,
    T1,
    T2,
    T3,
    T4,
    cancelling,
    pending,
    retry_pending,
    running,
    terminal,
)
from src.base.operations.v2.transitions import (
    fail_before_start,
    finish_attempt,
    request_cancellation,
    start_attempt,
)


@pytest_asyncio.fixture(params=["memory", "mongo"])
async def repository(request: Any) -> AsyncIterator[OperationRepository]:
    if request.param == "memory":
        yield InMemoryOperationRepository()
        return
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 to exercise configured MongoDB")

    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    # Each case owns a disposable database, dropped whole; no shared data is touched.
    async with isolated_mongo_repository("ops2") as infrastructure:
        yield MongoOperationRepository(infrastructure, f"ops2_test_{uuid4().hex}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        pending(),
        running(),
        retry_pending(),
        cancelling(),
        *(terminal(state) for state in OperationState if state.terminal),
        fail_before_start(pending(), at=T1, reason="preparation failed"),
    ],
)
async def test_create_get_round_trip(
    repository: OperationRepository, source: OperationRecord
) -> None:
    await repository.create(source)
    assert await repository.get(source.operation_id) == source
    assert OperationRecord.model_validate_json(source.model_dump_json()) == source
    restored = snapshot_from_document(BSON.encode(snapshot_to_document(source)).decode())
    assert restored == source
    # Mutating a returned object through a trusted bypass must not mutate storage.
    returned = await repository.get(source.operation_id)
    object.__setattr__(returned, "producer", "tampered")
    assert await repository.get(source.operation_id) == source


@pytest.mark.asyncio
async def test_duplicate_never_overwrites(repository: OperationRepository) -> None:
    initial = pending()
    await repository.create(initial)
    with pytest.raises(DuplicateOperation):
        await repository.create(running())
    assert await repository.get(initial.operation_id) == initial


@pytest.mark.asyncio
async def test_concurrent_create(repository: OperationRepository) -> None:
    results = await asyncio.gather(
        repository.create(pending()), repository.create(running()), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, DuplicateOperation) for result in results) == 1


@pytest.mark.asyncio
async def test_single_writer_retries_and_terminal_persistence(
    repository: OperationRepository,
) -> None:
    previous = pending()
    await repository.create(previous)
    second = start_attempt(retry_pending(), at=T3, attempt_id=SECOND)
    final = finish_attempt(second, attempt_id=SECOND, outcome=AttemptState.SUCCEEDED, at=T4)
    for following in (running(), retry_pending(), second, final):
        await repository.commit(following, expected_revision=previous.revision)
        assert await repository.get(previous.operation_id) == following
        previous = following
    with pytest.raises(InvalidSnapshot):
        await repository.commit(final.model_copy(update={"revision": 5}), expected_revision=4)
    assert await repository.get(final.operation_id) == final


@pytest.mark.asyncio
async def test_two_writers_exactly_one_wins(repository: OperationRepository) -> None:
    await repository.create(pending())
    first = running()
    second = start_attempt(pending(), at=T1, attempt_id=SECOND)
    results = await asyncio.gather(
        repository.commit(first, expected_revision=0),
        repository.commit(second, expected_revision=0),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, RevisionConflict) for result in results) == 1
    assert await repository.get(first.operation_id) in (first, second)
    with pytest.raises(RevisionConflict):
        await repository.commit(first, expected_revision=0)


@pytest.mark.asyncio
async def test_missing_read_and_commit(repository: OperationRepository) -> None:
    with pytest.raises(OperationNotFound):
        await repository.get(pending().operation_id)
    with pytest.raises(OperationNotFound):
        await repository.commit(running(), expected_revision=0)
    with pytest.raises(OperationNotFound):
        await repository.get(pending().operation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,following",
    [
        (pending(), fail_before_start(pending(), at=T1, reason="preparation failed")),
        (pending(), request_cancellation(pending(), at=T1, reason="stop before start")),
        (retry_pending(), request_cancellation(retry_pending(), at=T3, reason="stop while idle")),
    ],
)
async def test_idle_terminal_commits(
    repository: OperationRepository, previous: OperationRecord, following: OperationRecord
) -> None:
    await repository.create(previous)
    await repository.commit(following, expected_revision=previous.revision)
    assert await repository.get(previous.operation_id) == following


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, 2, -1, True, "1"])
async def test_revision_skips_and_malformed_snapshots(
    repository: OperationRepository, revision: Any
) -> None:
    await repository.create(pending())
    with pytest.raises(InvalidSnapshot):
        await repository.commit(
            running().model_copy(update={"revision": revision}), expected_revision=0
        )
    assert await repository.get(pending().operation_id) == pending()


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [-1, True, "0", 0.0])
async def test_invalid_expected_revision(repository: OperationRepository, expected: Any) -> None:
    await repository.create(pending())
    with pytest.raises(InvalidSnapshot):
        await repository.commit(running(), expected_revision=expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("producer", "other"), ("kind", "other"), ("correlation_id", "other")]
)
async def test_structurally_valid_metadata_rewrites_rejected(
    repository: OperationRepository, field: str, value: str
) -> None:
    await repository.create(pending())
    changed = OperationRecord.model_validate({**running().model_dump(), field: value})
    with pytest.raises(InvalidSnapshot):
        await repository.commit(changed, expected_revision=0)
    assert await repository.get(changed.operation_id) == pending()


@pytest.mark.asyncio
async def test_attempt_history_rewrite_rejected(repository: OperationRepository) -> None:
    waiting = retry_pending()
    await repository.create(waiting)
    data = start_attempt(waiting, at=T3, attempt_id=SECOND).model_dump()
    data["attempts"][0]["reason"] = "rewritten"
    with pytest.raises(InvalidSnapshot):
        await repository.commit(OperationRecord.model_validate(data), expected_revision=2)
    assert await repository.get(waiting.operation_id) == waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_wins", [False, True])
@pytest.mark.parametrize(
    "outcome", [AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.CANCELLED]
)
async def test_cancellation_completion_race(
    repository: OperationRepository, cancel_wins: bool, outcome: AttemptState
) -> None:
    active = running()
    await repository.create(active)
    requested = request_cancellation(active, at=T2, reason="stop")
    completed = finish_attempt(
        active,
        attempt_id=FIRST,
        outcome=outcome,
        at=T2,
        reason=None if outcome == AttemptState.SUCCEEDED else "actual outcome",
    )
    winner, loser = (requested, completed) if cancel_wins else (completed, requested)
    await repository.commit(winner, expected_revision=1)
    with pytest.raises(RevisionConflict):
        await repository.commit(loser, expected_revision=1)
    if cancel_wins:
        winner = finish_attempt(
            requested,
            attempt_id=FIRST,
            outcome=outcome,
            at=T3,
            reason=None if outcome == AttemptState.SUCCEEDED else "actual outcome",
        )
        await repository.commit(winner, expected_revision=2)
        assert winner.cancellation_reason == "stop"
    assert await repository.get(active.operation_id) == winner


def test_timestamp_microseconds_survive_bson() -> None:
    source = OperationRecord.create(
        kind="test", producer="test", at=T0 + timedelta(microseconds=123456)
    )
    assert snapshot_from_document(BSON.encode(snapshot_to_document(source)).decode()) == source


@pytest.mark.parametrize(
    "damage",
    ["identity", "missing_id", "missing_metadata", "revision", "version", "extra", "attempt"],
)
def test_corrupt_document_rejected(damage: str) -> None:
    document = snapshot_to_document(running())
    if damage == "identity":
        document["_id"] = str(uuid4())
    elif damage == "missing_id":
        del document["operation_id"]
    elif damage == "missing_metadata":
        del document["cancellation_reason"]
    elif damage == "revision":
        document["revision"] = "1"
    elif damage == "version":
        document["schema_version"] = True
    elif damage == "extra":
        document["execution_permit"] = "not authority"
    else:
        document["attempts"][0]["attempt_id"] = "invalid"
    with pytest.raises(CorruptSnapshot):
        snapshot_from_document(json.loads(json.dumps(document)))


@pytest.mark.asyncio
async def test_corrupt_database_snapshot_rejected(repository: OperationRepository) -> None:
    if not isinstance(repository, MongoOperationRepository):
        pytest.skip("Stored-document corruption applies to Mongo adapter")
    await repository.create(running())
    collection = await repository._collection()
    await collection.update_one(
        {"_id": str(running().operation_id)}, {"$unset": {"operation_id": ""}}
    )
    with pytest.raises(CorruptSnapshot):
        await repository.get(running().operation_id)
    with pytest.raises(CorruptSnapshot):
        await repository.commit(cancelling(), expected_revision=1)


@pytest.mark.asyncio
async def test_independent_mongo_writers_race_after_reading_same_revision(
    repository: OperationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not isinstance(repository, MongoOperationRepository):
        pytest.skip("Independent database adapters apply to Mongo")
    await repository.create(pending())
    other = MongoOperationRepository(repository._mongodb_repository, repository._collection_name)
    ready = asyncio.Event()
    reads = 0
    original_get = repository.get

    async def synchronized_get(operation_id: Any) -> OperationRecord:
        nonlocal reads
        snapshot = await original_get(operation_id)
        if snapshot.revision == 0:
            reads += 1
            if reads == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        return snapshot

    with monkeypatch.context() as context:
        context.setattr(repository, "get", synchronized_get)
        context.setattr(other, "get", synchronized_get)
        results = await asyncio.gather(
            repository.commit(running(), expected_revision=0),
            other.commit(start_attempt(pending(), at=T1, attempt_id=SECOND), expected_revision=0),
            return_exceptions=True,
        )
    assert reads == 2
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, RevisionConflict) for result in results) == 1
    # A fresh adapter restores the winner, not an adapter-local cache.
    assert (await other.get(pending().operation_id)).revision == 1
    collection = await repository._collection()
    indexes = await collection.index_information()
    assert indexes["_id_"]["key"] == [("_id", 1)]


@pytest.mark.asyncio
async def test_create_revalidates_bypassed_snapshot(repository: OperationRepository) -> None:
    with pytest.raises(InvalidSnapshot):
        await repository.create(running().model_copy(update={"attempts": ()}))
    with pytest.raises(OperationNotFound):
        await repository.get(pending().operation_id)


@pytest.mark.asyncio
async def test_database_errors_propagate_without_adapter_retry() -> None:
    collection = MagicMock()
    collection.write_concern.acknowledged = True
    collection.with_options.return_value = collection
    collection.insert_one = AsyncMock(side_effect=AutoReconnect("unavailable"))
    infrastructure = MagicMock(get_collection=AsyncMock(return_value=collection))
    repository = MongoOperationRepository(infrastructure)
    with pytest.raises(AutoReconnect):
        await repository.create(pending())
    collection.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_cas_filter_no_upsert_and_late_conflict() -> None:
    collection = MagicMock()
    collection.write_concern.acknowledged = True
    collection.with_options.return_value = collection
    collection.find_one = AsyncMock(
        side_effect=[snapshot_to_document(pending()), snapshot_to_document(running())]
    )
    collection.replace_one = AsyncMock(return_value=MagicMock(matched_count=0))
    repository = MongoOperationRepository(
        MagicMock(get_collection=AsyncMock(return_value=collection))
    )
    with pytest.raises(RevisionConflict):
        await repository.commit(running(), expected_revision=0)
    collection.replace_one.assert_awaited_once_with(
        {
            "_id": str(pending().operation_id),
            "schema_version": 1,
            "revision": 0,
            "snapshot": {"$exists": False},
            "execution": {"$exists": False},
        },
        snapshot_to_document(running()),
        upsert=False,
    )


def test_di_reuses_client_and_leaves_v1_independent() -> None:
    from src.base.dependencies.di_container import Container
    from src.base.operations.repository import InMemoryOperationRepository as V1Repository

    container = Container()
    legacy = container.operations.operation_repository()
    current = container.operations.operation_repository_v2()
    assert isinstance(legacy, V1Repository)
    assert isinstance(current, MongoOperationRepository)
    assert current._mongodb_repository.client is container.database.mongo_client()
    assert current is container.operations.operation_repository_v2()
    container.operations.event_publisher.override(MagicMock())
    assert legacy is container.operations.operation_service().repository
