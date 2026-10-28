"""Ownership adapter contracts, with real Mongo integration behind explicit opt-in."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from bson import BSON
from bson.codec_options import CodecOptions
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from src.base.operations.v2.memory_ownership_repository import InMemoryOwnershipRepository
from src.base.operations.v2.mongo_ownership_repository import (
    MongoOwnershipRepository,
    ownership_from_document,
    ownership_to_document,
)
from src.base.operations.v2.ownership import (
    MAX_FENCING_TOKEN,
    CorruptOwnership,
    InvalidOwnershipRequest,
    LeaseExpired,
    ObsoleteFencingToken,
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipRecord,
    OwnershipReleased,
    OwnershipRepository,
    OwnershipState,
)

OPERATION, ATTEMPT, NEXT_ATTEMPT = (UUID(int=i) for i in (101, 102, 103))
LEASE_MS = 30_000
T0 = datetime(2026, 10, 28, tzinfo=UTC)


@dataclass
class Clock:
    now: datetime = T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Backend:
    repository: OwnershipRepository
    other: OwnershipRepository
    clock: Clock | None

    async def expire(self) -> None:
        if self.clock is not None:
            self.clock.now += timedelta(milliseconds=100)
        else:
            # Exercise actual Mongo time, not application-clock comparisons or
            # edited lease documents. Claims in these cases use 100ms leases.
            await asyncio.sleep(0.2)


@pytest_asyncio.fixture(params=["memory", "mongo"])
async def backend(request: Any) -> AsyncIterator[Backend]:
    if request.param == "memory":
        clock = Clock()
        repository = InMemoryOwnershipRepository(clock)
        yield Backend(repository, repository, clock)
        return
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real configured MongoDB")
    from src.base.dependencies.containers.db_container import DatabaseContainer

    database = DatabaseContainer()
    infrastructure = database.mongodb_repository()
    name = f"ops3_test_{uuid4().hex}"
    collection = await infrastructure.get_collection(name)
    try:
        yield Backend(
            MongoOwnershipRepository(infrastructure, name),
            MongoOwnershipRepository(infrastructure, name),
            None,
        )
    finally:
        try:
            await collection.drop()
        finally:
            await database.mongo_client().disconnect()


async def claim(
    repository: OwnershipRepository,
    *,
    owner: str = "worker-a",
    attempt: UUID = ATTEMPT,
    lease_ms: int = LEASE_MS,
) -> OwnershipRecord:
    return await repository.claim(
        OPERATION,
        attempt_id=attempt,
        owner_id=owner,
        lease_ms=lease_ms,
    )


@pytest.mark.asyncio
async def test_first_claim_and_inspect(backend: Backend) -> None:
    record = await claim(backend.repository)
    assert record.operation_id == OPERATION and record.attempt_id == ATTEMPT
    assert record.owner_id == "worker-a" and record.fencing_token == 1
    assert record.state == OwnershipState.ACTIVE and record.released_at is None
    assert record.created_at == record.acquired_at == record.updated_at
    assert record.lease_expires_at - record.acquired_at == timedelta(milliseconds=LEASE_MS)
    assert await backend.other.inspect(OPERATION) == record
    assert OwnershipRecord.model_validate_json(record.model_dump_json(), strict=True) == record
    document = BSON.encode(ownership_to_document(record)).decode(
        codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
    )
    assert ownership_from_document(document) == record
    object.__setattr__(record, "owner_id", "tampered")
    assert (await backend.other.inspect(OPERATION)).owner_id == "worker-a"


@pytest.mark.asyncio
async def test_competing_first_claims(backend: Backend) -> None:
    results = await asyncio.gather(
        claim(backend.repository),
        claim(backend.other, owner="worker-b"),
        return_exceptions=True,
    )
    winners = [value for value in results if isinstance(value, OwnershipRecord)]
    assert len(winners) == 1
    assert sum(isinstance(value, OwnershipConflict) for value in results) == 1
    assert await backend.other.inspect(OPERATION) == winners[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,attempt", [("worker-a", ATTEMPT), ("worker-b", NEXT_ATTEMPT)])
async def test_unexpired_cannot_be_claimed(backend: Backend, owner: str, attempt: UUID) -> None:
    record = await claim(backend.repository)
    with pytest.raises(OwnershipConflict):
        await claim(backend.other, owner=owner, attempt=attempt)
    assert await backend.repository.inspect(OPERATION) == record


@pytest.mark.asyncio
async def test_renew_retains_generation_and_never_shortens(backend: Backend) -> None:
    initial = await claim(backend.repository)
    renewed = await backend.other.renew(
        OPERATION,
        owner_id="worker-a",
        fencing_token=1,
        lease_ms=LEASE_MS * 2,
    )
    assert renewed.fencing_token == initial.fencing_token
    assert renewed.created_at == initial.created_at
    assert renewed.acquired_at == initial.acquired_at
    assert renewed.attempt_id == initial.attempt_id
    assert renewed.updated_at >= initial.updated_at
    assert renewed.lease_expires_at > initial.lease_expires_at
    shorter = await backend.repository.renew(
        OPERATION,
        owner_id="worker-a",
        fencing_token=1,
        lease_ms=1,
    )
    assert shorter.lease_expires_at == renewed.lease_expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["renew", "release"])
@pytest.mark.parametrize(
    "owner,token,error",
    [("worker-b", 1, OwnershipConflict), ("worker-a", 2, ObsoleteFencingToken)],
)
async def test_wrong_owner_or_token(
    backend: Backend, action: str, owner: str, token: int, error: type[Exception]
) -> None:
    initial = await claim(backend.repository)
    method = getattr(backend.other, action)
    options = {"lease_ms": LEASE_MS} if action == "renew" else {}
    with pytest.raises(error):
        await method(OPERATION, owner_id=owner, fencing_token=token, **options)
    assert await backend.repository.inspect(OPERATION) == initial


@pytest.mark.asyncio
async def test_expiration_takeover_and_stale_owner_rejection(backend: Backend) -> None:
    previous = await claim(backend.repository, lease_ms=100)
    await backend.expire()
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(LeaseExpired):
            await getattr(backend.other, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )
    assert await backend.repository.inspect(OPERATION) == previous
    current = await claim(backend.other, owner="worker-b", attempt=NEXT_ATTEMPT)
    assert current.fencing_token == 2
    assert current.created_at == previous.created_at
    assert current.attempt_id == NEXT_ATTEMPT
    assert current.acquired_at >= previous.lease_expires_at
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(ObsoleteFencingToken):
            await getattr(backend.repository, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )
    with pytest.raises(OwnershipConflict):
        await claim(backend.repository)
    await backend.other.release(OPERATION, owner_id="worker-b", fencing_token=2)
    # Even the original owner must receive a new token after a later valid claim.
    assert (await claim(backend.repository)).fencing_token == 3


@pytest.mark.asyncio
async def test_release_is_retained_and_not_revivable(backend: Backend) -> None:
    first = await claim(backend.repository)
    released = await backend.other.release(OPERATION, owner_id="worker-a", fencing_token=1)
    assert released.state == OwnershipState.RELEASED
    assert released.fencing_token == first.fencing_token
    assert released.released_at == released.updated_at
    assert released.lease_expires_at == first.lease_expires_at
    assert await backend.repository.inspect(OPERATION) == released
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(OwnershipReleased):
            await getattr(backend.other, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )
    next_record = await claim(backend.other, owner="worker-b")
    assert next_record.fencing_token == 2 and next_record.released_at is None


@pytest.mark.asyncio
async def test_concurrent_expired_takeover_has_one_winner(backend: Backend) -> None:
    await claim(backend.repository, lease_ms=100)
    await backend.expire()
    results = await asyncio.gather(
        claim(backend.repository, owner="worker-b"),
        claim(backend.other, owner="worker-c", attempt=NEXT_ATTEMPT),
        return_exceptions=True,
    )
    winners = [value for value in results if isinstance(value, OwnershipRecord)]
    assert len(winners) == 1 and winners[0].fencing_token == 2
    assert sum(isinstance(value, OwnershipConflict) for value in results) == 1
    assert await backend.repository.inspect(OPERATION) == winners[0]


@pytest.mark.asyncio
async def test_missing_ownership(backend: Backend) -> None:
    with pytest.raises(OwnershipNotFound):
        await backend.repository.inspect(OPERATION)
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(OwnershipNotFound):
            await getattr(backend.repository, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0, -1, True, "100", 1.5, None, 86_400_001])
async def test_invalid_durations_do_not_write(backend: Backend, duration: Any) -> None:
    with pytest.raises(InvalidOwnershipRequest):
        await claim(backend.repository, lease_ms=duration)
    record = await claim(backend.repository)
    with pytest.raises(InvalidOwnershipRequest):
        await backend.other.renew(
            OPERATION,
            owner_id="worker-a",
            fencing_token=1,
            lease_ms=duration,
        )
    assert await backend.repository.inspect(OPERATION) == record


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [0, -1, True, "1", 1.5, MAX_FENCING_TOKEN + 1])
async def test_invalid_tokens(backend: Backend, token: Any) -> None:
    await claim(backend.repository)
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(InvalidOwnershipRequest):
            await getattr(backend.other, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=token,
                **options,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["", "   ", None, 123])
async def test_invalid_owners(backend: Backend, owner: Any) -> None:
    with pytest.raises(InvalidOwnershipRequest):
        await claim(backend.repository, owner=owner)
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(InvalidOwnershipRequest):
            await getattr(backend.other, action)(
                OPERATION,
                owner_id=owner,
                fencing_token=1,
                **options,
            )


def valid_record() -> OwnershipRecord:
    return OwnershipRecord(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        owner_id="worker-a",
        fencing_token=1,
        state=OwnershipState.ACTIVE,
        created_at=T0,
        acquired_at=T0,
        updated_at=T0,
        lease_expires_at=T0 + timedelta(seconds=1),
        released_at=None,
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"operation_id": "not-uuid"},
        {"attempt_id": OPERATION},
        {"owner_id": " "},
        {"fencing_token": 0},
        {"fencing_token": True},
        {"fencing_token": "1"},
        {"fencing_token": MAX_FENCING_TOKEN + 1},
        {"state": "unknown"},
        {"created_at": T0.replace(tzinfo=None)},
        {"acquired_at": T0 - timedelta(seconds=1)},
        {"updated_at": T0 + timedelta(seconds=1)},
        {"lease_expires_at": T0},
        {"lease_expires_at": T0 + timedelta(microseconds=1)},
        {"state": OwnershipState.RELEASED},
        {"released_at": T0},
        {"execution_permit": "no"},
    ],
)
def test_invalid_records_rejected(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OwnershipRecord.model_validate({**valid_record().model_dump(), **updates})


@pytest.mark.parametrize("field", list(OwnershipRecord.model_fields))
def test_restoration_requires_all_fields(field: str) -> None:
    data = valid_record().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        OwnershipRecord.model_validate(data)


@pytest.mark.parametrize("damage", ["date_type", "token_type", "identity", "schema", "missing"])
def test_corrupt_storage_is_not_repaired(damage: str) -> None:
    document = ownership_to_document(valid_record())
    if damage == "date_type":
        document["lease_expires_at"] = document["lease_expires_at"].isoformat()
    elif damage == "token_type":
        document["fencing_token"] = "1"
    elif damage == "identity":
        document["_id"] = str(uuid4())
    elif damage == "schema":
        document["schema_version"] = True
    else:
        del document["released_at"]
    with pytest.raises(CorruptOwnership):
        ownership_from_document(document)


def test_record_serialization_revalidates_trusted_bypass() -> None:
    with pytest.raises(ValidationError):
        ownership_to_document(valid_record().model_copy(update={"fencing_token": 0}))


@pytest.mark.asyncio
async def test_repository_clock_boundary_and_regression() -> None:
    clock = Clock()
    repository = InMemoryOwnershipRepository(clock)
    await claim(repository, lease_ms=100)
    clock.now = T0 - timedelta(seconds=1)
    with pytest.raises(OwnershipConflict):
        await repository.renew(OPERATION, owner_id="worker-a", fencing_token=1, lease_ms=LEASE_MS)
    clock.now = T0 + timedelta(milliseconds=100)
    with pytest.raises(LeaseExpired):
        await repository.renew(OPERATION, owner_id="worker-a", fencing_token=1, lease_ms=LEASE_MS)
    assert (await claim(repository)).fencing_token == 2


@pytest.mark.asyncio
async def test_owner_text_is_data_not_pipeline_expression(backend: Backend) -> None:
    await claim(backend.repository)
    await backend.repository.release(OPERATION, owner_id="worker-a", fencing_token=1)
    current = await claim(backend.other, owner="$fencing_token")
    assert current.owner_id == "$fencing_token" and current.fencing_token == 2


@pytest.mark.asyncio
async def test_mongo_reconstruction_and_corruption(backend: Backend) -> None:
    if not isinstance(backend.repository, MongoOwnershipRepository):
        pytest.skip("Stored Mongo reconstruction/corruption case")
    record = await claim(backend.repository)
    reconstructed = MongoOwnershipRepository(
        backend.repository._mongodb_repository, backend.repository._collection_name
    )
    assert await reconstructed.inspect(OPERATION) == record
    released = await reconstructed.release(OPERATION, owner_id="worker-a", fencing_token=1)
    reconstructed = MongoOwnershipRepository(
        backend.repository._mongodb_repository, backend.repository._collection_name
    )
    assert await reconstructed.inspect(OPERATION) == released
    assert (await claim(reconstructed, owner="worker-b")).fencing_token == 2
    collection = await backend.repository._collection()
    assert collection.write_concern.document == {"w": "majority", "j": True, "wtimeout": 5000}
    assert (await collection.index_information())["_id_"]["key"] == [("_id", 1)]
    await collection.update_one({"_id": str(OPERATION)}, {"$unset": {"fencing_token": ""}})
    for method in (backend.repository.inspect, backend.other.inspect):
        with pytest.raises(CorruptOwnership):
            await method(OPERATION)
    with pytest.raises(CorruptOwnership):
        await claim(backend.other)
    assert "fencing_token" not in await collection.find_one({"_id": str(OPERATION)})


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover", [False, True])
async def test_mongo_claim_races_from_identical_prereads(
    backend: Backend, takeover: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not isinstance(backend.repository, MongoOwnershipRepository):
        pytest.skip("Independent Mongo claim race")
    if takeover:
        await claim(backend.repository, lease_ms=100)
        await backend.expire()
    ready = asyncio.Event()
    reads = 0
    original_read = backend.repository._read

    async def synchronized_read(operation_id: UUID) -> tuple[OwnershipRecord, datetime]:
        nonlocal reads
        try:
            result = await original_read(operation_id)
        except OwnershipNotFound:
            result = None
        # Synchronize the two pre-reads, not the winner's post-insert check.
        if reads < 2:
            reads += 1
            if reads == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        if result is None:
            raise OwnershipNotFound(operation_id)
        return result

    with monkeypatch.context() as context:
        context.setattr(backend.repository, "_read", synchronized_read)
        context.setattr(backend.other, "_read", synchronized_read)
        results = await asyncio.gather(
            claim(backend.repository, owner="worker-b"),
            claim(backend.other, owner="worker-c"),
            return_exceptions=True,
        )
    assert reads == 2
    winners = [value for value in results if isinstance(value, OwnershipRecord)]
    assert len(winners) == 1
    assert winners[0].fencing_token == (2 if takeover else 1)
    assert sum(isinstance(value, OwnershipConflict) for value in results) == 1
    assert await backend.other.inspect(OPERATION) == winners[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("superseded", [False, True])
async def test_mongo_initial_insert_outlasts_lease(
    backend: Backend, superseded: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not isinstance(backend.repository, MongoOwnershipRepository):
        pytest.skip("Mongo initial-insert timing case")
    collection = await backend.repository._collection()
    original_insert = collection.insert_one
    inserted: dict[str, Any] = {}
    insert_calls = 0

    async def delayed_insert(document: dict[str, Any]) -> Any:
        nonlocal insert_calls
        insert_calls += 1
        inserted.update(document)
        # Delay a real Mongo insert, not a mocked database response or clock.
        await asyncio.sleep(0.2)
        result = await original_insert(document)
        if superseded:
            replacement = await claim(backend.other, owner="worker-b", attempt=NEXT_ATTEMPT)
            assert replacement.fencing_token == 2
        return result

    async def same_collection() -> Any:
        return collection

    with monkeypatch.context() as context:
        context.setattr(backend.repository, "_collection", same_collection)
        context.setattr(collection, "insert_one", delayed_insert)
        with pytest.raises(ObsoleteFencingToken if superseded else LeaseExpired):
            await claim(backend.repository, lease_ms=100)
    assert insert_calls == 1  # No automatic claim retry.
    retained, observed_at = await backend.repository._read(OPERATION)
    if not superseded:
        assert retained == ownership_from_document(inserted)
        assert retained.lease_expires_at <= observed_at
        assert await backend.repository.inspect(OPERATION) == retained
        retained = await claim(backend.other, owner="worker-b", attempt=NEXT_ATTEMPT)
    assert retained.fencing_token == 2
    assert retained.owner_id == "worker-b" and retained.attempt_id == NEXT_ATTEMPT
    assert await backend.repository.inspect(OPERATION) == retained
    for action in ("renew", "release"):
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(ObsoleteFencingToken):
            await getattr(backend.repository, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )
    assert await backend.other.inspect(OPERATION) == retained


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["renew", "release"])
async def test_mongo_expiration_between_validation_and_write(
    backend: Backend, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not isinstance(backend.repository, MongoOwnershipRepository):
        pytest.skip("Mongo server-time conditional write case")
    initial = await claim(backend.repository, lease_ms=500)
    original_read = backend.repository._read
    first = True

    async def delayed_read(operation_id: UUID) -> tuple[OwnershipRecord, datetime]:
        nonlocal first
        result = await original_read(operation_id)
        if first:
            first = False
            assert result[1] < result[0].lease_expires_at
            await asyncio.sleep(0.6)
        return result

    with monkeypatch.context() as context:
        context.setattr(backend.repository, "_read", delayed_read)
        options = {"lease_ms": LEASE_MS} if action == "renew" else {}
        with pytest.raises(LeaseExpired):
            await getattr(backend.repository, action)(
                OPERATION,
                owner_id="worker-a",
                fencing_token=1,
                **options,
            )
    assert await backend.other.inspect(OPERATION) == initial


@pytest.mark.asyncio
async def test_token_exhaustion_never_wraps(backend: Backend) -> None:
    await claim(backend.repository)
    released = await backend.repository.release(OPERATION, owner_id="worker-a", fencing_token=1)
    exhausted = OwnershipRecord.model_validate(
        {
            **released.model_dump(),
            "fencing_token": MAX_FENCING_TOKEN,
        }
    )
    if isinstance(backend.repository, InMemoryOwnershipRepository):
        backend.repository._records[OPERATION] = exhausted
    else:
        collection = await backend.repository._collection()
        await collection.replace_one({"_id": str(OPERATION)}, ownership_to_document(exhausted))
    with pytest.raises(InvalidOwnershipRequest):
        await claim(backend.other)
    assert await backend.repository.inspect(OPERATION) == exhausted


@pytest.mark.asyncio
async def test_mongo_error_is_not_automatically_retried() -> None:
    collection = MagicMock()
    collection.with_options.return_value = collection
    collection.aggregate.return_value.to_list = AsyncMock(side_effect=AutoReconnect("unavailable"))
    repository = MongoOwnershipRepository(
        MagicMock(get_collection=AsyncMock(return_value=collection))
    )
    with pytest.raises(AutoReconnect):
        await claim(repository)
    collection.aggregate.return_value.to_list.assert_awaited_once()


def test_di_preserves_v1_and_snapshot_repositories() -> None:
    from src.base.dependencies.di_container import Container
    from src.base.operations.repository import InMemoryOperationRepository as V1Repository
    from src.base.operations.v2.mongo_repository import MongoOperationRepository

    container = Container()
    ownership = container.operations.ownership_repository_v2()
    assert isinstance(ownership, MongoOwnershipRepository)
    assert ownership is container.operations.ownership_repository_v2()
    assert ownership._mongodb_repository.client is container.database.mongo_client()
    assert isinstance(container.operations.operation_repository(), V1Repository)
    assert isinstance(container.operations.operation_repository_v2(), MongoOperationRepository)
