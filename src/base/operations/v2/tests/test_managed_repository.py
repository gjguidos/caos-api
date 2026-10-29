"""Managed aggregate contracts and real single-document Mongo race tests."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from bson import BSON
from bson.codec_options import CodecOptions
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from src.base.operations.v2.managed import (
    AmbiguousManagedWrite,
    AttemptConflict,
    CorruptManagedOperation,
    EpochMode,
    EpochStatus,
    ExecutionEpoch,
    InvalidManagedRequest,
    ManagedOperation,
    ReconciliationOnly,
    ReconciliationRequired,
    StateChangeReceipt,
)
from src.base.operations.v2.models import AttemptState, OperationRecord, OperationState
from src.base.operations.v2.mongo_managed_repository import (
    MongoManagedOperationRepository,
    managed_from_document,
    managed_to_document,
)
from src.base.operations.v2.mongo_repository import MongoOperationRepository, snapshot_to_document
from src.base.operations.v2.ownership import (
    MAX_FENCING_TOKEN,
    InvalidOwnershipRequest,
    LeaseExpired,
    ObsoleteFencingToken,
    OwnershipConflict,
    OwnershipReleased,
)
from src.base.operations.v2.repository import (
    CorruptSnapshot,
    DuplicateOperation,
    InvalidSnapshot,
    OperationNotFound,
    RevisionConflict,
)
from src.base.operations.v2.transitions import InvalidTransition, finish_attempt, start_attempt

OP, FIRST, SECOND, OTHER_OP = (UUID(int=i) for i in (201, 202, 203, 204))
T0 = datetime(2026, 10, 28, tzinfo=UTC)
T1, T2, T3, T4 = (T0 + timedelta(seconds=i) for i in range(1, 5))
LEASE_MS = 30_000


def pending(operation_id: UUID = OP) -> OperationRecord:
    return OperationRecord.create(
        operation_id=operation_id,
        kind="test",
        producer="test",
        at=T0,
        correlation_id="correlation",
        causation_id="parent",
    )


@pytest_asyncio.fixture
async def repositories() -> AsyncIterator[
    tuple[MongoManagedOperationRepository, MongoManagedOperationRepository]
]:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo managed aggregate tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    # A disposable database, dropped whole: never the configured application DB.
    async with isolated_mongo_repository("ops4") as infrastructure:
        name = f"ops4_test_{uuid4().hex}"
        yield (
            MongoManagedOperationRepository(infrastructure, name),
            MongoManagedOperationRepository(infrastructure, name),
        )


async def activate(
    repository: MongoManagedOperationRepository, **overrides: Any
) -> ManagedOperation:
    options = {
        "expected_revision": 0,
        "expected_generation": 0,
        "attempt_id": FIRST,
        "owner_id": "worker-a",
        "lease_ms": LEASE_MS,
        "at": T1,
        "command_id": uuid4(),
    }
    options.update(overrides)
    operation_id = options.pop("operation_id", OP)
    return await repository.activate(operation_id, **options)


async def active(repository: MongoManagedOperationRepository, **options: Any) -> ManagedOperation:
    await repository.create(pending(options.get("operation_id", OP)))
    return await activate(repository, **options)


def complete(
    record: ManagedOperation,
    *,
    outcome: AttemptState = AttemptState.SUCCEEDED,
    retry: bool = False,
    at: datetime = T2,
) -> OperationRecord:
    return finish_attempt(
        record.snapshot,
        attempt_id=record.snapshot.attempts[-1].attempt_id,
        outcome=outcome,
        reason=None if outcome == AttemptState.SUCCEEDED else "actual outcome",
        retry=retry,
        at=at,
    )


async def commit(
    repository: MongoManagedOperationRepository, record: ManagedOperation, **overrides: Any
) -> ManagedOperation:
    options = {
        "expected_revision": record.snapshot.revision,
        "attempt_id": record.execution.attempt_id,
        "owner_id": record.execution.owner_id,
        "generation": record.execution.generation,
        "command_id": uuid4(),
    }
    options.update(overrides)
    following = options.pop("following") if "following" in options else complete(record)
    return await repository.commit_outcome(following, **options)


async def renew(repository: MongoManagedOperationRepository, **overrides: Any) -> ManagedOperation:
    options = {
        "attempt_id": FIRST,
        "owner_id": "worker-a",
        "generation": 1,
        "lease_ms": LEASE_MS * 2,
    }
    options.update(overrides)
    operation_id = options.pop("operation_id", OP)
    return await repository.renew(operation_id, **options)


async def release(
    repository: MongoManagedOperationRepository, **overrides: Any
) -> ManagedOperation:
    options = {"attempt_id": FIRST, "owner_id": "worker-a", "generation": 1}
    options.update(overrides)
    return await repository.release(OP, **options)


async def takeover(
    repository: MongoManagedOperationRepository, **overrides: Any
) -> ManagedOperation:
    options = {
        "expected_revision": 1,
        "expected_generation": 1,
        "attempt_id": FIRST,
        "owner_id": "worker-b",
        "lease_ms": LEASE_MS,
    }
    options.update(overrides)
    return await repository.takeover(OP, **options)


def pause_write(
    repository: MongoManagedOperationRepository, monkeypatch: pytest.MonkeyPatch
) -> tuple[asyncio.Event, asyncio.Event]:
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = repository._write

    async def paused(*args: Any, **kwargs: Any) -> ManagedOperation:
        entered.set()
        await asyncio.wait_for(proceed.wait(), timeout=5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(repository, "_write", paused)
    return entered, proceed


def synchronize_reads(
    first: MongoManagedOperationRepository,
    second: MongoManagedOperationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, ready, count = first._read, asyncio.Event(), 0

    async def synchronized(operation_id: UUID) -> tuple[ManagedOperation, datetime]:
        nonlocal count
        result = await original(operation_id)
        if count < 2:
            count += 1
            if count == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        return result

    monkeypatch.setattr(first, "_read", synchronized)
    monkeypatch.setattr(second, "_read", synchronized)


@pytest.mark.asyncio
async def test_creation_roundtrip_and_identity_uniqueness(repositories: Any) -> None:
    repository, other = repositories
    record = await repository.create(pending())
    assert record.execution == ExecutionEpoch.idle()
    assert await other.inspect(OP) == record
    collection = await repository._collection()
    document = await collection.find_one({"_id": str(OP)})
    assert "revision" not in document and document["schema_version"] == 2
    assert (
        managed_from_document(
            BSON.encode(document).decode(codec_options=CodecOptions(tz_aware=True, tzinfo=UTC))
        )
        == record
    )
    assert collection.write_concern.document == {"w": "majority", "j": True, "wtimeout": 5000}
    with pytest.raises(DuplicateOperation):
        await other.create(pending())
    legacy = MongoOperationRepository(repository._mongodb_repository, repository._collection_name)
    with pytest.raises(DuplicateOperation):
        await legacy.create(pending())
    with pytest.raises(CorruptSnapshot):
        await legacy.get(OP)
    with pytest.raises(OperationNotFound):
        await repository.inspect(OTHER_OP)


@pytest.mark.asyncio
async def test_competing_activation_atomic_start_and_epoch(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    await repository.create(pending())
    synchronize_reads(repository, other, monkeypatch)
    results = await asyncio.gather(
        activate(repository),
        activate(other, owner_id="worker-b", attempt_id=SECOND),
        return_exceptions=True,
    )
    winners = [value for value in results if isinstance(value, ManagedOperation)]
    assert len(winners) == 1
    assert (
        sum(isinstance(value, (ObsoleteFencingToken, RevisionConflict)) for value in results) == 1
    )
    winner = winners[0]
    assert winner.snapshot.revision == winner.execution.generation == 1
    assert winner.execution.attempt_id == winner.snapshot.attempts[-1].attempt_id
    assert winner.snapshot.state == OperationState.RUNNING
    assert await repository.inspect(OP) == winner


@pytest.mark.asyncio
async def test_competing_outcomes_commit_once(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository)
    synchronize_reads(repository, other, monkeypatch)
    results = await asyncio.gather(
        commit(repository, original),
        commit(other, original, following=complete(original, outcome=AttemptState.FAILED)),
        return_exceptions=True,
    )
    winners = [value for value in results if isinstance(value, ManagedOperation)]
    assert len(winners) == 1
    assert sum(isinstance(value, OwnershipReleased) for value in results) == 1
    assert winners[0].snapshot.revision == 2
    assert (
        winners[0].execution.generation == 1 and winners[0].execution.status == EpochStatus.CLOSED
    )
    assert await other.inspect(OP) == winners[0]


@pytest.mark.asyncio
async def test_retry_attempt_history_and_terminal_closure(repositories: Any) -> None:
    repository, other = repositories
    first = await active(repository)
    renewed = await renew(other)
    assert renewed.snapshot == first.snapshot
    assert renewed.execution.generation == 1
    assert renewed.execution.lease_expires_at > first.execution.lease_expires_at
    waiting = await commit(
        repository, first, following=complete(first, outcome=AttemptState.FAILED, retry=True)
    )
    assert waiting.snapshot.state == OperationState.RETRY_PENDING
    assert waiting.execution.status == EpochStatus.RELEASED
    second = await activate(
        other, expected_revision=2, expected_generation=1, attempt_id=SECOND, at=T3
    )
    assert second.execution.generation == 2
    assert [attempt.attempt_number for attempt in second.snapshot.attempts] == [1, 2]
    assert second.snapshot.attempts[0] == waiting.snapshot.attempts[0]
    final = await commit(repository, second, following=complete(second, at=T4))
    assert final.execution.status == EpochStatus.CLOSED and final.snapshot.revision == 4
    assert final.execution.generation == 2
    assert (await other.inspect(OP)) == final
    with pytest.raises(InvalidTransition):
        await activate(other, expected_revision=4, expected_generation=2, at=T4)
    with pytest.raises(InvalidTransition):
        await takeover(other, expected_revision=4, expected_generation=2, attempt_id=SECOND)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", list(AttemptState)[1:])
async def test_terminal_outcomes_reject_renew_release_and_replay(
    repositories: Any, outcome: AttemptState
) -> None:
    repository, other = repositories
    record = await active(repository)
    following = complete(record, outcome=outcome)
    command_id = uuid4()
    final = await commit(repository, record, following=following, command_id=command_id)
    assert final.snapshot == following and final.execution.status == EpochStatus.CLOSED
    assert final.last_state_change.command_id == command_id
    assert len(final.last_state_change.request_hash) == 64
    for request in (
        renew(other),
        release(other),
        commit(other, record, following=following, command_id=command_id),
    ):
        with pytest.raises(OwnershipReleased):
            await request
    assert await other.inspect(OP) == final


@pytest.mark.asyncio
async def test_release_and_reconciliation_never_authorize_replay(repositories: Any) -> None:
    repository, other = repositories
    original = await active(repository)
    released = await release(other)
    assert released.snapshot == original.snapshot
    assert released.execution.generation == 1
    with pytest.raises(OwnershipReleased):
        await renew(repository)
    with pytest.raises(InvalidTransition):
        await activate(repository, expected_revision=1, expected_generation=1, attempt_id=SECOND)
    reconciler = await takeover(other)
    assert reconciler.snapshot == original.snapshot
    assert reconciler.execution.mode == EpochMode.RECONCILE
    assert reconciler.execution.generation == 2 and reconciler.execution.attempt_id == FIRST
    with pytest.raises(ReconciliationOnly):
        await commit(other, reconciler)
    renewed = await renew(other, owner_id="worker-b", generation=2)
    assert renewed.snapshot == original.snapshot
    with pytest.raises(ObsoleteFencingToken):
        await commit(repository, original)  # Lifecycle revision still matches.
    with pytest.raises(ObsoleteFencingToken):
        await renew(repository)
    with pytest.raises(ObsoleteFencingToken):
        await release(repository)
    assert (await repository.inspect(OP)).execution.generation == 2
    retired = await release(other, owner_id="worker-b", generation=2)
    assert retired.execution.generation == 2 and retired.snapshot == original.snapshot
    next_reconciler = await takeover(other, expected_generation=2, owner_id="worker-c")
    assert next_reconciler.execution.generation == 3
    assert next_reconciler.execution.mode == EpochMode.RECONCILE
    assert next_reconciler.snapshot == original.snapshot
    with pytest.raises(ObsoleteFencingToken):
        await renew(repository, owner_id="worker-b", generation=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("owner_id", "worker-b", OwnershipConflict),
        ("attempt_id", SECOND, AttemptConflict),
        ("generation", 2, ObsoleteFencingToken),
        ("expected_revision", 0, InvalidSnapshot),
    ],
)
async def test_invalid_commit_credentials(
    repositories: Any, field: str, value: Any, error: type[Exception]
) -> None:
    repository, _ = repositories
    original = await active(repository)
    with pytest.raises(error):
        await commit(repository, original, **{field: value})
    assert await repository.inspect(OP) == original


@pytest.mark.asyncio
async def test_malformed_next_snapshot_and_foreign_attempts(repositories: Any) -> None:
    repository, _ = repositories
    original = await active(repository)
    following = complete(original)
    for changed in (
        following.model_copy(update={"revision": 3}),
        following.model_copy(update={"attempts": ()}),
        OperationRecord.model_validate({**following.model_dump(), "producer": "rewritten"}),
    ):
        with pytest.raises(InvalidSnapshot):
            await commit(repository, original, following=changed)
    with pytest.raises(AttemptConflict):
        await renew(repository, attempt_id=SECOND)
    with pytest.raises(AttemptConflict):
        await takeover(repository, attempt_id=SECOND)
    assert await repository.inspect(OP) == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", True),
        ("expected_generation", -1),
        ("command_id", "not-a-command-uuid"),
        ("lease_ms", 0),
        ("attempt_id", OP),
        ("owner_id", " "),
    ],
)
async def test_invalid_activation_requests_do_not_mutate(
    repositories: Any, field: str, value: Any
) -> None:
    repository, other = repositories
    initial = await repository.create(pending())
    with pytest.raises(InvalidOwnershipRequest):
        await activate(repository, **{field: value})
    assert await other.inspect(OP) == initial


@pytest.mark.asyncio
async def test_no_implicit_progressed_snapshot_import_or_missing_activation(
    repositories: Any,
) -> None:
    repository, _ = repositories
    with pytest.raises(OperationNotFound):
        await activate(repository)
    with pytest.raises(InvalidManagedRequest):
        await repository.create(start_attempt(pending(), at=T1, attempt_id=FIRST))
    with pytest.raises(OperationNotFound):
        await repository.inspect(OP)


@pytest.mark.asyncio
async def test_generation_exhaustion_is_retained(repositories: Any) -> None:
    repository, other = repositories
    await active(repository)
    await release(repository)
    collection = await repository._collection()
    # Test-only high-water-mark setup, not a reset or import API.
    await collection.update_one(
        {"_id": str(OP)}, {"$set": {"execution.generation": MAX_FENCING_TOKEN}}
    )
    with pytest.raises(InvalidManagedRequest):
        await takeover(other, expected_generation=MAX_FENCING_TOKEN)
    assert (await other.inspect(OP)).execution.generation == MAX_FENCING_TOKEN


@pytest.mark.asyncio
async def test_expiration_and_concurrent_reconciliation_takeover(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository, lease_ms=500)
    with pytest.raises(OwnershipConflict):
        await takeover(other)
    await asyncio.sleep(0.6)
    with pytest.raises(LeaseExpired):
        await commit(repository, original)
    synchronize_reads(repository, other, monkeypatch)
    results = await asyncio.gather(
        takeover(repository), takeover(other, owner_id="worker-c"), return_exceptions=True
    )
    winners = [value for value in results if isinstance(value, ManagedOperation)]
    assert len(winners) == 1 and winners[0].execution.generation == 2
    assert winners[0].execution.mode == EpochMode.RECONCILE
    assert winners[0].snapshot == original.snapshot
    assert sum(isinstance(value, ObsoleteFencingToken) for value in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["commit", "renew", "release"])
async def test_expiration_after_preread_before_conditional_write(
    repositories: Any, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository, lease_ms=500)
    entered, proceed = pause_write(repository, monkeypatch)
    request = (
        commit(repository, original)
        if action == "commit"
        else renew(repository)
        if action == "renew"
        else release(repository)
    )
    task = asyncio.create_task(request)
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.sleep(0.6)
    proceed.set()
    with pytest.raises(LeaseExpired):
        await task
    assert await other.inspect(OP) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover_first", [False, True])
async def test_completion_takeover_orderings(
    repositories: Any, takeover_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository, lease_ms=500)
    entered, proceed = pause_write(repository, monkeypatch)
    task = asyncio.create_task(
        commit(repository, original) if takeover_first else takeover(repository)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    if takeover_first:
        await asyncio.sleep(0.6)
        winner = await takeover(other)
    else:
        winner = await commit(other, original)
        await asyncio.sleep(0.6)
    proceed.set()
    with pytest.raises(ObsoleteFencingToken if takeover_first else RevisionConflict):
        await task
    assert await other.inspect(OP) == winner
    assert winner.execution.mode == (EpochMode.RECONCILE if takeover_first else EpochMode.EXECUTE)
    assert winner.snapshot.state == (
        OperationState.RUNNING if takeover_first else OperationState.SUCCEEDED
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover_first", [False, True])
async def test_renewal_takeover_orderings(
    repositories: Any, takeover_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository, lease_ms=500)
    entered, proceed = pause_write(repository, monkeypatch)
    task = asyncio.create_task(renew(repository) if takeover_first else takeover(repository))
    await asyncio.wait_for(entered.wait(), timeout=5)
    if takeover_first:
        await asyncio.sleep(0.6)
        winner = await takeover(other)
    else:
        winner = await renew(other)
        await asyncio.sleep(0.6)
    proceed.set()
    with pytest.raises(ObsoleteFencingToken if takeover_first else OwnershipConflict):
        await task
    assert await other.inspect(OP) == winner
    assert winner.snapshot == original.snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("release_first", [False, True])
async def test_release_completion_orderings(
    repositories: Any, release_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository)
    entered, proceed = pause_write(repository, monkeypatch)
    task = asyncio.create_task(
        commit(repository, original) if release_first else release(repository)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    winner = await release(other) if release_first else await commit(other, original)
    proceed.set()
    with pytest.raises(OwnershipReleased):
        await task
    assert await other.inspect(OP) == winner


@pytest.mark.asyncio
async def test_distinct_operations_renew_independently(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    first = await active(repository)
    second = await active(other, operation_id=OTHER_OP, attempt_id=SECOND)
    entered, proceed = pause_write(repository, monkeypatch)
    task = asyncio.create_task(renew(repository))
    await asyncio.wait_for(entered.wait(), timeout=5)
    renewed_second = await asyncio.wait_for(
        renew(other, operation_id=OTHER_OP, attempt_id=SECOND), timeout=2
    )
    assert renewed_second.snapshot == second.snapshot
    assert renewed_second.execution.generation == 1
    assert renewed_second.execution.lease_expires_at > second.execution.lease_expires_at
    proceed.set()
    renewed_first = await task
    assert renewed_first.snapshot == first.snapshot and renewed_first.execution.generation == 1


@pytest.mark.asyncio
async def test_heartbeat_does_not_overwrite_or_conflict_with_completion(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository)
    entered, proceed = pause_write(repository, monkeypatch)
    task = asyncio.create_task(commit(repository, original))
    await asyncio.wait_for(entered.wait(), timeout=5)
    renewed = await renew(other)
    proceed.set()
    final = await task
    assert final.snapshot.revision == 2 and final.execution.status == EpochStatus.CLOSED
    assert final.execution.lease_expires_at == renewed.execution.lease_expires_at


@pytest.mark.asyncio
async def test_schema1_is_not_adopted_or_overwritten(repositories: Any) -> None:
    repository, _ = repositories
    legacy = MongoOperationRepository(repository._mongodb_repository, repository._collection_name)
    await legacy.create(pending())
    with pytest.raises(DuplicateOperation):
        await repository.create(pending())
    with pytest.raises(CorruptManagedOperation):
        await repository.inspect(OP)
    with pytest.raises(CorruptManagedOperation):
        await activate(repository)
    assert await legacy.get(OP) == pending()


@pytest.mark.asyncio
async def test_separate_ownership_record_is_not_managed_authority(repositories: Any) -> None:
    from src.base.operations.v2.mongo_ownership_repository import MongoOwnershipRepository

    repository, _ = repositories
    original = await active(repository)
    # An isolated legacy collection can issue its own token; it grants nothing
    # against the managed aggregate, which never consults that collection.
    name = repository._collection_name + "_legacy"
    legacy = MongoOwnershipRepository(repository._mongodb_repository, name)
    collection = await legacy._collection()
    try:
        token = await legacy.claim(OP, attempt_id=FIRST, owner_id="worker-b", lease_ms=LEASE_MS)
        assert token.fencing_token == original.execution.generation == 1
        with pytest.raises(OwnershipConflict):
            await commit(repository, original, owner_id="worker-b", generation=token.fencing_token)
        assert await repository.inspect(OP) == original
    finally:
        await collection.drop()


@pytest.mark.asyncio
async def test_corrupt_stored_aggregate_is_never_repaired_by_mutation(repositories: Any) -> None:
    repository, other = repositories
    await active(repository)
    collection = await repository._collection()
    await collection.update_one({"_id": str(OP)}, {"$unset": {"execution.generation": ""}})
    with pytest.raises(CorruptManagedOperation):
        await other.inspect(OP)
    with pytest.raises(CorruptManagedOperation):
        await renew(other)
    assert "generation" not in (await collection.find_one({"_id": str(OP)}))["execution"]


@pytest.mark.asyncio
async def test_old_and_hardened_inflight_replacement_writers_cannot_erase_epoch(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _ = repositories
    collection = await repository._collection()
    legacy = MongoOperationRepository(repository._mongodb_repository, repository._collection_name)
    await legacy.create(pending())
    cached = await legacy.get(OP)
    # Test-only cutover setup, not a migration API: old writer has already read v1.
    envelope = managed_to_document(
        ManagedOperation(
            schema_version=2,
            snapshot=pending(),
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
        )
    )
    await collection.replace_one({"_id": str(OP)}, envelope)
    result = await collection.replace_one(
        {"_id": str(OP), "revision": 0},
        snapshot_to_document(start_attempt(cached, at=T1, attempt_id=FIRST)),
        upsert=False,
    )
    assert result.matched_count == 0
    actual_get, reads = legacy.get, 0

    async def cached_then_current(operation_id: UUID) -> OperationRecord:
        nonlocal reads
        reads += 1
        return cached if reads == 1 else await actual_get(operation_id)

    monkeypatch.setattr(legacy, "get", cached_then_current)
    with pytest.raises(CorruptSnapshot):
        await legacy.commit(start_attempt(cached, at=T1, attempt_id=FIRST), expected_revision=0)
    assert await collection.find_one({"_id": str(OP)}) == envelope
    # Even a malformed envelope with a forbidden compatibility revision alias
    # cannot match the upgraded writer's explicit schema-v1 predicate.
    await collection.update_one({"_id": str(OP)}, {"$set": {"revision": 0}})
    reads = 0
    with pytest.raises(CorruptSnapshot):
        await legacy.commit(start_attempt(cached, at=T1, attempt_id=FIRST), expected_revision=0)
    assert (await collection.find_one({"_id": str(OP)}))["schema_version"] == 2


@pytest.mark.asyncio
async def test_ambiguous_applied_outcome_is_retained_not_retried(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    original = await active(repository)
    collection = await repository._collection()
    actual_write, calls = collection.find_one_and_update, 0
    command_id = uuid4()

    async def applied_then_error(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        await actual_write(*args, **kwargs)
        raise AutoReconnect("test response lost after actual Mongo write")

    async def same_collection() -> Any:
        return collection

    monkeypatch.setattr(repository, "_collection", same_collection)
    monkeypatch.setattr(collection, "find_one_and_update", applied_then_error)
    with pytest.raises(AmbiguousManagedWrite) as error:
        await commit(repository, original, command_id=command_id)
    assert calls == 1 and error.value.command_id == command_id and error.value.operation_id == OP
    stored = await other.inspect(OP)
    assert stored.snapshot.state == OperationState.SUCCEEDED
    assert stored.last_state_change.command_id == command_id
    with pytest.raises(RevisionConflict):
        await takeover(other)


@pytest.mark.asyncio
async def test_delayed_activation_observation_preserves_unresolved_attempt(
    repositories: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, other = repositories
    await repository.create(pending())
    actual_write = repository._write

    async def delayed_response(*args: Any, **kwargs: Any) -> ManagedOperation:
        record = await actual_write(*args, **kwargs)
        await asyncio.sleep(0.6)
        return record

    monkeypatch.setattr(repository, "_write", delayed_response)
    with pytest.raises(LeaseExpired):
        await activate(repository, lease_ms=500)
    stored = await other.inspect(OP)
    assert stored.snapshot.revision == 1 and stored.execution.generation == 1
    replacement = await takeover(other)
    assert replacement.snapshot == stored.snapshot
    assert (
        replacement.execution.generation == 2 and replacement.execution.mode == EpochMode.RECONCILE
    )


def sample_aggregate() -> ManagedOperation:
    return ManagedOperation(
        schema_version=2,
        snapshot=start_attempt(pending(), at=T1, attempt_id=FIRST),
        execution=ExecutionEpoch(
            generation=1,
            owner_id="worker-a",
            attempt_id=FIRST,
            status=EpochStatus.ACTIVE,
            mode=EpochMode.EXECUTE,
            acquired_at=T0,
            updated_at=T0,
            lease_expires_at=T0 + timedelta(seconds=30),
            released_at=None,
        ),
        last_state_change=StateChangeReceipt(
            command_id=uuid4(),
            action="activate",
            request_hash="0" * 64,
            generation=1,
            attempt_id=FIRST,
            previous_revision=0,
            resulting_revision=1,
        ),
    )


@pytest.mark.parametrize(
    "damage",
    [
        "revision_alias",
        "schema",
        "identity",
        "snapshot_field",
        "attempt_field",
        "epoch_field",
        "epoch_attempt",
        "receipt",
        "clock_type",
        "mode",
    ],
)
def test_strict_managed_restoration(damage: str) -> None:
    record = sample_aggregate()
    assert ManagedOperation.model_validate_json(record.model_dump_json(), strict=True) == record
    document = managed_to_document(record)
    if damage == "revision_alias":
        document["revision"] = 1
    elif damage == "schema":
        document["schema_version"] = "2"
    elif damage == "identity":
        document["_id"] = str(OTHER_OP)
    elif damage == "snapshot_field":
        del document["snapshot"]["correlation_id"]
    elif damage == "attempt_field":
        del document["snapshot"]["attempts"][0]["reason"]
    elif damage == "epoch_field":
        del document["execution"]["released_at"]
    elif damage == "epoch_attempt":
        document["execution"]["attempt_id"] = str(SECOND)
    elif damage == "receipt":
        document["last_state_change"]["resulting_revision"] = 2
    elif damage == "clock_type":
        document["execution"]["updated_at"] = T0.isoformat()
    else:
        document["execution"]["mode"] = "permission"
    with pytest.raises(CorruptManagedOperation):
        managed_from_document(document)


def test_trusted_bypass_and_epoch_inconsistency_rejected() -> None:
    with pytest.raises(ValidationError):
        managed_to_document(
            sample_aggregate().model_copy(update={"execution": ExecutionEpoch.idle()})
        )


def test_managed_di_reuses_client_and_preserves_existing_providers() -> None:
    from src.base.dependencies.di_container import Container
    from src.base.operations.repository import InMemoryOperationRepository
    from src.base.operations.v2.mongo_ownership_repository import MongoOwnershipRepository

    container = Container()
    repository = container.operations.managed_operation_repository_v2()
    assert repository._mongodb_repository.client is container.database.mongo_client()
    assert repository is container.operations.managed_operation_repository_v2()
    assert isinstance(container.operations.operation_repository(), InMemoryOperationRepository)
    assert isinstance(container.operations.operation_repository_v2(), MongoOperationRepository)
    assert isinstance(container.operations.ownership_repository_v2(), MongoOwnershipRepository)


# --- Verified reconciliation resolution -------------------------------------------------------


async def resolve(
    repository: MongoManagedOperationRepository, **overrides: Any
) -> ManagedOperation:
    options = {
        "expected_revision": 1,
        "attempt_id": FIRST,
        "owner_id": "worker-b",
        "generation": 2,
        "reason": "interrupted attempt reconciled",
        "at": T2,
        "command_id": uuid4(),
    }
    options.update(overrides)
    return await repository.resolve_reconciled(OP, **options)


async def interrupted(repository: MongoManagedOperationRepository, other: Any) -> ManagedOperation:
    """FIRST was activated by worker-a, which stopped; worker-b took it over."""
    await active(repository)
    await release(other)  # same eligibility for takeover as an expired lease
    return await takeover(other)


@pytest.mark.asyncio
async def test_reconciliation_resolves_interrupted_attempt_to_retry_pending(
    repositories: Any,
) -> None:
    repository, other = repositories
    reconciler = await interrupted(repository, other)
    resolved = await resolve(other)
    snapshot = resolved.snapshot
    assert snapshot.state == OperationState.RETRY_PENDING and snapshot.revision == 2
    (attempt,) = snapshot.attempts
    assert (attempt.attempt_id, attempt.state, attempt.reason) == (
        FIRST,
        AttemptState.FAILED,
        "interrupted attempt reconciled",
    )
    assert resolved.execution.status == EpochStatus.RELEASED
    assert resolved.execution.generation == reconciler.execution.generation == 2
    receipt = resolved.last_state_change
    assert receipt is not None and (receipt.action, receipt.attempt_id) == ("outcome", FIRST)
    # A fresh attempt can now execute; history keeps the reconciled attempt.
    fresh = await activate(
        repository, expected_revision=2, expected_generation=2, attempt_id=SECOND, at=T3
    )
    assert [a.attempt_id for a in fresh.snapshot.attempts] == [FIRST, SECOND]
    assert fresh.execution.mode == EpochMode.EXECUTE and fresh.execution.generation == 3


@pytest.mark.asyncio
async def test_resolution_requires_reconciliation_ownership(repositories: Any) -> None:
    repository, _ = repositories
    await active(repository)
    with pytest.raises(ReconciliationRequired):
        await resolve(repository, owner_id="worker-a", generation=1)
    assert (await repository.inspect(OP)).snapshot.state == OperationState.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("owner_id", "worker-a", OwnershipConflict),
        ("attempt_id", SECOND, AttemptConflict),
        ("generation", 1, ObsoleteFencingToken),
        ("expected_revision", 0, RevisionConflict),
        ("reason", " ", InvalidManagedRequest),
    ],
)
async def test_resolution_rejects_wrong_credentials_without_mutation(
    repositories: Any, field: str, value: Any, error: type[Exception]
) -> None:
    repository, other = repositories
    reconciler = await interrupted(repository, other)
    with pytest.raises(error):
        await resolve(other, **{field: value})
    assert await other.inspect(OP) == reconciler


@pytest.mark.asyncio
async def test_superseded_reconciler_and_newer_attempts_cannot_be_overwritten(
    repositories: Any,
) -> None:
    repository, other = repositories
    await interrupted(repository, other)
    await release(other, owner_id="worker-b", generation=2)
    await takeover(other, expected_generation=2, owner_id="worker-c")
    with pytest.raises(ObsoleteFencingToken):
        await resolve(other)  # worker-b's generation was superseded
    await resolve(other, owner_id="worker-c", generation=3)
    await activate(repository, expected_revision=2, expected_generation=3, attempt_id=SECOND, at=T3)
    newer = await repository.inspect(OP)
    for stale in ({"owner_id": "worker-c", "generation": 3}, {"expected_revision": 2}):
        with pytest.raises(
            (ObsoleteFencingToken, AttemptConflict, OwnershipConflict, RevisionConflict)
        ):
            await resolve(other, **stale)
    assert await repository.inspect(OP) == newer


@pytest.mark.asyncio
async def test_concurrent_resolutions_apply_once(repositories: Any) -> None:
    repository, other = repositories
    await interrupted(repository, other)
    results = await asyncio.gather(resolve(other), resolve(repository), return_exceptions=True)
    assert sum(not isinstance(r, BaseException) for r in results) == 1
    assert (await repository.inspect(OP)).snapshot.revision == 2
