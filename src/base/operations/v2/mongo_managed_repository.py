"""One authoritative operation/epoch document; native MongoDB conditional writes."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from bson.codec_options import CodecOptions
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

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
    StateChangeReceipt,
)
from src.base.operations.v2.models import OperationRecord, OperationState
from src.base.operations.v2.mongo_repository import COLLECTION_NAME, snapshot_from_document
from src.base.operations.v2.ownership import (
    MAX_FENCING_TOKEN,
    LeaseExpired,
    ObsoleteFencingToken,
    OwnershipConflict,
    OwnershipReleased,
    validate_claim,
    validate_duration,
    validate_identity,
    validate_key,
)
from src.base.operations.v2.repository import (
    DuplicateOperation,
    InvalidSnapshot,
    OperationNotFound,
    RevisionConflict,
    validate_revision,
    validate_snapshot,
    validate_successor,
)
from src.base.operations.v2.transitions import InvalidTransition, start_attempt
from src.base.repositories.mongodb_repository import MongoDBRepository

SCHEMA_VERSION = 2
_CODEC = CodecOptions(tz_aware=True, tzinfo=UTC)
_UNRESOLVED = [OperationState.RUNNING.value, OperationState.CANCEL_REQUESTED.value]
_EPOCH_DATES = ("acquired_at", "updated_at", "lease_expires_at", "released_at")


def managed_to_document(record: ManagedOperation) -> dict[str, Any]:
    record = ManagedOperation.model_validate(record, strict=True)
    data = record.model_dump(mode="json")
    # Snapshot JSON retains microseconds; only coordination clocks are BSON dates.
    for field in _EPOCH_DATES:
        data["execution"][field] = getattr(record.execution, field)
    return {"_id": str(record.snapshot.operation_id), **data}


def _date_json(value: Any) -> str:
    if not isinstance(value, datetime):
        raise TypeError("Expected BSON datetime")
    return value.isoformat()


def managed_from_document(document: dict[str, Any]) -> ManagedOperation:
    try:
        data = dict(document)
        identity = data.pop("_id")
        if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Not a schema-v2 managed operation; no implicit migration")
        if set(data) != set(ManagedOperation.model_fields):
            raise ValueError("Incomplete or unknown aggregate fields")
        # Preserve OPS2-02's complete snapshot/attempt restoration checks.
        snapshot_from_document({"_id": identity, "schema_version": 1, **data["snapshot"]})
        if set(data["execution"]) != set(ExecutionEpoch.model_fields):
            raise ValueError("Incomplete or unknown execution fields")
        for field in _EPOCH_DATES:
            at = data["execution"][field]
            if at is not None and (not isinstance(at, datetime) or at.tzinfo is None):
                raise ValueError("Execution clocks must be aware BSON dates")
        receipt = data["last_state_change"]
        if receipt is not None and set(receipt) != set(StateChangeReceipt.model_fields):
            raise ValueError("Incomplete or unknown receipt fields")
        record = ManagedOperation.model_validate_json(
            json.dumps(data, default=_date_json), strict=True
        )
        if identity != str(record.snapshot.operation_id):
            raise ValueError("Storage and operation identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptManagedOperation("Invalid managed operation envelope") from exc


def _expected(revision: int, generation: int) -> None:
    if type(revision) is not int or revision < 0:
        raise InvalidManagedRequest("Expected revision must be a nonnegative integer")
    if type(generation) is not int or not 0 <= generation <= MAX_FENCING_TOKEN:
        raise InvalidManagedRequest("Expected generation must be a nonnegative int64")


def _command(command_id: UUID) -> None:
    if not isinstance(command_id, UUID):
        raise InvalidManagedRequest("State mutations require a caller-retained command UUID")


def _key(operation_id: UUID, attempt_id: UUID, owner_id: str, generation: int) -> None:
    validate_key(operation_id, owner_id, generation)
    if not isinstance(attempt_id, UUID) or attempt_id == operation_id:
        raise InvalidManagedRequest("A distinct current attempt UUID is required")


class MongoManagedOperationRepository:
    """No executor, recovery, separate ownership authority, migration or write retry."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = COLLECTION_NAME
    ) -> None:
        self._mongodb_repository = mongodb_repository
        self._collection_name = collection_name

    async def _collection(self) -> Any:
        collection = await self._mongodb_repository.get_collection(self._collection_name)
        return collection.with_options(
            codec_options=_CODEC,
            read_preference=ReadPreference.PRIMARY,
            read_concern=ReadConcern("majority"),
            write_concern=WriteConcern(w="majority", j=True, wtimeout=5000),
        )

    async def _read(self, operation_id: UUID) -> tuple[ManagedOperation, datetime]:
        validate_identity(operation_id)
        collection = await self._collection()
        rows = await collection.aggregate(
            [
                {"$match": {"_id": str(operation_id)}},
                {"$project": {"_id": 0, "record": "$$ROOT", "now": "$$NOW"}},
            ]
        ).to_list(length=1)
        if not rows:
            raise OperationNotFound(operation_id)
        return managed_from_document(rows[0]["record"]), rows[0]["now"]

    async def inspect(self, operation_id: UUID) -> ManagedOperation:
        record, _ = await self._read(operation_id)
        return record

    async def create(self, operation: OperationRecord) -> ManagedOperation:
        operation = validate_snapshot(operation)
        if operation.state != OperationState.PENDING:
            raise InvalidManagedRequest("Only new pending operations can be created as managed")
        record = ManagedOperation(
            schema_version=2,
            snapshot=operation,
            execution=ExecutionEpoch.idle(),
            last_state_change=None,
        )
        collection = await self._collection()
        try:
            await collection.insert_one(managed_to_document(record))
        except DuplicateKeyError as exc:
            raise DuplicateOperation(operation.operation_id) from exc
        except PyMongoError as exc:
            raise AmbiguousManagedWrite(operation.operation_id, None) from exc
        return record

    @staticmethod
    def _base(operation_id: UUID) -> dict[str, Any]:
        return {
            "_id": str(operation_id),
            "schema_version": SCHEMA_VERSION,
            "snapshot.operation_id": str(operation_id),
            "revision": {"$exists": False},
        }

    @staticmethod
    def _versions(record: ManagedOperation, revision: int | None, generation: int) -> None:
        if record.execution.generation != generation:
            raise ObsoleteFencingToken(record.snapshot.operation_id)
        if revision is not None and record.snapshot.revision != revision:
            raise RevisionConflict(record.snapshot.operation_id)

    @staticmethod
    def _live(
        record: ManagedOperation,
        now: datetime,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        *,
        execute: bool = False,
    ) -> None:
        epoch = record.execution
        MongoManagedOperationRepository._versions(record, None, generation)
        if epoch.owner_id != owner_id:
            raise OwnershipConflict(record.snapshot.operation_id)
        if epoch.attempt_id != attempt_id:
            raise AttemptConflict(attempt_id)
        if epoch.status != EpochStatus.ACTIVE:
            raise OwnershipReleased(record.snapshot.operation_id)
        if execute and epoch.mode != EpochMode.EXECUTE:
            raise ReconciliationOnly(record.snapshot.operation_id)
        assert epoch.lease_expires_at is not None and epoch.updated_at is not None
        if epoch.lease_expires_at <= now:
            raise LeaseExpired(record.snapshot.operation_id)
        if epoch.updated_at > now:
            raise OwnershipConflict("Database clock moved backwards")

    def _live_predicate(
        self, operation_id: UUID, attempt_id: UUID, owner_id: str, generation: int
    ) -> dict[str, Any]:
        return {
            **self._base(operation_id),
            "execution.generation": generation,
            "execution.owner_id": owner_id,
            "execution.attempt_id": str(attempt_id),
            "execution.status": EpochStatus.ACTIVE.value,
            "snapshot.state": {"$in": _UNRESOLVED},
            "$expr": {
                "$and": [
                    {
                        "$eq": [
                            {"$arrayElemAt": ["$snapshot.attempts.attempt_id", -1]},
                            str(attempt_id),
                        ]
                    },
                    {"$gt": ["$execution.lease_expires_at", "$$NOW"]},
                    {"$gte": ["$$NOW", "$execution.updated_at"]},
                ]
            },
        }

    @staticmethod
    def _receipt(
        record: ManagedOperation,
        following: OperationRecord,
        command_id: UUID,
        action: str,
        generation: int,
        **request: Any,
    ) -> dict[str, Any]:
        if (
            record.last_state_change is not None
            and record.last_state_change.command_id == command_id
        ):
            raise InvalidManagedRequest(
                "Inspect the retained receipt instead of replaying a command"
            )
        payload = {
            "action": action,
            "previous_revision": record.snapshot.revision,
            "previous_generation": record.execution.generation,
            "snapshot": following.model_dump(mode="json"),
            **request,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "command_id": str(command_id),
            "action": action,
            "request_hash": digest,
            "generation": generation,
            "attempt_id": str(following.attempts[-1].attempt_id),
            "previous_revision": record.snapshot.revision,
            "resulting_revision": following.revision,
        }

    async def _write(
        self,
        operation_id: UUID,
        predicate: dict[str, Any],
        updates: dict[str, Any],
        *,
        generation: int,
        revision: int | None = None,
        command_id: UUID | None = None,
        live_key: tuple[UUID, str] | None = None,
        execute: bool = False,
    ) -> ManagedOperation:
        collection = await self._collection()
        try:
            document = await collection.find_one_and_update(
                predicate,
                [{"$set": updates}],
                upsert=False,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise AmbiguousManagedWrite(operation_id, command_id) from exc
        if document is None:
            # Classification only: this read never authorizes a mutation or retries it.
            current, now = await self._read(operation_id)
            if live_key is not None:
                self._live(current, now, *live_key, generation, execute=execute)
            self._versions(current, revision, generation)
            raise OwnershipConflict(
                "Conditional managed mutation lost; inspect before a new request"
            )
        return managed_from_document(document)

    async def _observed_grant(
        self,
        operation_id: UUID,
        attempt_id: UUID,
        owner_id: str,
        generation: int,
        command_id: UUID | None = None,
    ) -> ManagedOperation:
        try:
            current, now = await self._read(operation_id)
        except PyMongoError as exc:
            raise AmbiguousManagedWrite(operation_id, command_id) from exc
        self._live(current, now, attempt_id, owner_id, generation)
        return current  # Valid at observation only; every subsequent write fences again.

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
        validate_claim(operation_id, attempt_id, owner_id, lease_ms)
        _expected(expected_revision, expected_generation)
        _command(command_id)
        previous, _ = await self._read(operation_id)
        self._versions(previous, expected_revision, expected_generation)
        if expected_generation == MAX_FENCING_TOKEN:
            raise InvalidManagedRequest("Generation space exhausted; never reset")
        following = start_attempt(previous.snapshot, at=at, attempt_id=attempt_id)
        predicate = {
            **self._base(operation_id),
            "snapshot.revision": expected_revision,
            "snapshot.state": {"$in": ["pending", "retry_pending"]},
            "execution.generation": expected_generation,
            "execution.status": {"$in": ["idle", "released"]},
            "$expr": {
                "$or": [
                    {"$eq": ["$execution.updated_at", None]},
                    {"$gte": ["$$NOW", "$execution.updated_at"]},
                ]
            },
        }
        receipt = self._receipt(
            previous,
            following,
            command_id,
            "activate",
            expected_generation + 1,
            owner_id=owner_id,
            lease_ms=lease_ms,
        )
        await self._write(
            operation_id,
            predicate,
            {
                "snapshot": {"$literal": following.model_dump(mode="json")},
                "execution": self._new_epoch(attempt_id, owner_id, lease_ms, EpochMode.EXECUTE),
                "last_state_change": {"$literal": receipt},
            },
            generation=expected_generation,
            revision=expected_revision,
            command_id=command_id,
        )
        return await self._observed_grant(
            operation_id, attempt_id, owner_id, expected_generation + 1, command_id
        )

    @staticmethod
    def _new_epoch(
        attempt_id: UUID, owner_id: str, lease_ms: int, mode: EpochMode
    ) -> dict[str, Any]:
        return {
            "generation": {"$add": ["$execution.generation", 1]},
            "owner_id": {"$literal": owner_id},
            "attempt_id": {"$literal": str(attempt_id)},
            "status": EpochStatus.ACTIVE.value,
            "mode": mode.value,
            "acquired_at": "$$NOW",
            "updated_at": "$$NOW",
            "lease_expires_at": {"$add": ["$$NOW", lease_ms]},
            "released_at": None,
        }

    async def renew(
        self, operation_id: UUID, *, attempt_id: UUID, owner_id: str, generation: int, lease_ms: int
    ) -> ManagedOperation:
        _key(operation_id, attempt_id, owner_id, generation)
        validate_duration(lease_ms)
        previous, now = await self._read(operation_id)
        self._live(previous, now, attempt_id, owner_id, generation)
        await self._write(
            operation_id,
            self._live_predicate(operation_id, attempt_id, owner_id, generation),
            {
                "execution.updated_at": "$$NOW",
                "execution.lease_expires_at": {
                    "$max": ["$execution.lease_expires_at", {"$add": ["$$NOW", lease_ms]}]
                },
            },
            generation=generation,
            live_key=(attempt_id, owner_id),
        )
        return await self._observed_grant(operation_id, attempt_id, owner_id, generation)

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
        operation = validate_snapshot(operation)
        operation_id = operation.operation_id
        _key(operation_id, attempt_id, owner_id, generation)
        _expected(expected_revision, generation)
        _command(command_id)
        validate_revision(operation, expected_revision)
        previous, now = await self._read(operation_id)
        self._live(previous, now, attempt_id, owner_id, generation, execute=True)
        self._versions(previous, expected_revision, generation)
        if operation.state not in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.RETRY_PENDING,
        }:
            raise InvalidSnapshot("This command records attempt outcomes only")
        validate_successor(previous.snapshot, operation)
        predicate = self._live_predicate(operation_id, attempt_id, owner_id, generation)
        predicate.update(
            {"snapshot.revision": expected_revision, "execution.mode": EpochMode.EXECUTE.value}
        )
        receipt = self._receipt(
            previous, operation, command_id, "outcome", generation, owner_id=owner_id
        )
        return await self._write(
            operation_id,
            predicate,
            {
                "snapshot": {"$literal": operation.model_dump(mode="json")},
                "execution.status": EpochStatus.CLOSED.value
                if operation.state.terminal
                else EpochStatus.RELEASED.value,
                "execution.updated_at": "$$NOW",
                "execution.released_at": "$$NOW",
                "last_state_change": {"$literal": receipt},
            },
            generation=generation,
            revision=expected_revision,
            command_id=command_id,
            live_key=(attempt_id, owner_id),
            execute=True,
        )

    async def release(
        self, operation_id: UUID, *, attempt_id: UUID, owner_id: str, generation: int
    ) -> ManagedOperation:
        _key(operation_id, attempt_id, owner_id, generation)
        previous, now = await self._read(operation_id)
        self._live(previous, now, attempt_id, owner_id, generation)
        return await self._write(
            operation_id,
            self._live_predicate(operation_id, attempt_id, owner_id, generation),
            {
                "execution.status": EpochStatus.RELEASED.value,
                "execution.updated_at": "$$NOW",
                "execution.released_at": "$$NOW",
            },
            generation=generation,
            live_key=(attempt_id, owner_id),
        )

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
        validate_claim(operation_id, attempt_id, owner_id, lease_ms)
        _expected(expected_revision, expected_generation)
        previous, _ = await self._read(operation_id)
        self._versions(previous, expected_revision, expected_generation)
        if previous.snapshot.state not in {OperationState.RUNNING, OperationState.CANCEL_REQUESTED}:
            raise InvalidTransition("Only unresolved work can acquire reconciliation ownership")
        if previous.execution.attempt_id != attempt_id:
            raise AttemptConflict(attempt_id)
        if expected_generation == MAX_FENCING_TOKEN:
            raise InvalidManagedRequest("Generation space exhausted; never reset")
        predicate = {
            **self._base(operation_id),
            "snapshot.revision": expected_revision,
            "snapshot.state": {"$in": _UNRESOLVED},
            "execution.generation": expected_generation,
            "execution.attempt_id": str(attempt_id),
            "execution.status": {"$in": ["active", "released"]},
            "$expr": {
                "$and": [
                    {
                        "$eq": [
                            {"$arrayElemAt": ["$snapshot.attempts.attempt_id", -1]},
                            str(attempt_id),
                        ]
                    },
                    {"$gte": ["$$NOW", "$execution.updated_at"]},
                    {
                        "$or": [
                            {"$eq": ["$execution.status", "released"]},
                            {"$lte": ["$execution.lease_expires_at", "$$NOW"]},
                        ]
                    },
                ]
            },
        }
        await self._write(
            operation_id,
            predicate,
            {
                "execution": self._new_epoch(attempt_id, owner_id, lease_ms, EpochMode.RECONCILE),
            },
            generation=expected_generation,
            revision=expected_revision,
        )
        return await self._observed_grant(
            operation_id, attempt_id, owner_id, expected_generation + 1
        )
