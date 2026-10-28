"""Single-document lease CAS, using native MongoDB server time and retained tokens."""

import json
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID

from bson.codec_options import CodecOptions
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from src.base.operations.v2.ownership import (
    MAX_FENCING_TOKEN,
    CorruptOwnership,
    InvalidOwnershipRequest,
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipRecord,
    OwnershipState,
    new_claim,
    require_live,
    validate_claim,
    validate_duration,
    validate_identity,
    validate_key,
)
from src.base.repositories.mongodb_repository import MongoDBRepository

COLLECTION_NAME = "operation_ownership_v2"
SCHEMA_VERSION = 1
_CODEC = CodecOptions(tz_aware=True, tzinfo=UTC)


def ownership_to_document(record: OwnershipRecord) -> dict[str, Any]:
    record = OwnershipRecord.model_validate(record, strict=True)
    data = record.model_dump()
    data.update(
        operation_id=str(record.operation_id),
        attempt_id=str(record.attempt_id),
        state=record.state.value,
    )
    return {"_id": str(record.operation_id), "schema_version": SCHEMA_VERSION, **data}


def _json_datetime(value: Any) -> str:
    if not isinstance(value, datetime):
        raise TypeError("Expected BSON datetime")
    return value.isoformat()


def ownership_from_document(document: dict[str, Any]) -> OwnershipRecord:
    try:
        data = dict(document)
        identity, version = data.pop("_id"), data.pop("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("Unsupported ownership schema")
        if set(data) != set(OwnershipRecord.model_fields):
            raise ValueError("Incomplete or unknown ownership fields")
        for field in ("created_at", "acquired_at", "updated_at", "lease_expires_at", "released_at"):
            value = data[field]
            if field == "released_at" and value is None:
                continue
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("Ownership storage requires aware BSON dates")
        record = OwnershipRecord.model_validate_json(
            json.dumps(data, default=_json_datetime), strict=True
        )
        if identity != str(record.operation_id):
            raise ValueError("Storage and ownership identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptOwnership("Invalid stored ownership") from exc


class MongoOwnershipRepository:
    """No TTL, delete, global counter, transactions or application retry loop.

    Pre-reads validate stored snapshots; exact-snapshot CAS plus $$NOW is the
    write boundary. A unique insert handles absence, never an upsert takeover.
    """

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

    async def _read(self, operation_id: UUID) -> tuple[OwnershipRecord, datetime]:
        collection = await self._collection()
        rows = await collection.aggregate(
            [
                {"$match": {"_id": str(operation_id)}},
                {"$project": {"_id": 0, "record": "$$ROOT", "now": "$$NOW"}},
            ]
        ).to_list(length=1)
        if not rows:
            raise OwnershipNotFound(operation_id)
        return ownership_from_document(rows[0]["record"]), rows[0]["now"]

    async def inspect(self, operation_id: UUID) -> OwnershipRecord:
        validate_identity(operation_id)
        record, _ = await self._read(operation_id)
        return record

    async def claim(
        self, operation_id: UUID, *, attempt_id: UUID, owner_id: str, lease_ms: int
    ) -> OwnershipRecord:
        validate_claim(operation_id, attempt_id, owner_id, lease_ms)
        collection = await self._collection()
        try:
            previous, _ = await self._read(operation_id)
        except OwnershipNotFound:
            # Only the initial insertion needs a separate server-time sample.
            # Unique _id insertion is the atomic gate: absence is never assumed.
            hello = await collection.database.command(
                "hello",
                codec_options=_CODEC,
                read_preference=ReadPreference.PRIMARY,
            )
            record = new_claim(
                operation_id,
                attempt_id=attempt_id,
                owner_id=owner_id,
                lease_ms=lease_ms,
                now=hello["localTime"],
            )
            try:
                await collection.insert_one(ownership_to_document(record))
            except DuplicateKeyError as exc:
                raise OwnershipConflict(operation_id) from exc
            # Insertion/acknowledgement may outlast the sampled lease, and a
            # takeover may already have won. Check the persisted generation
            # against database time without deleting, resetting or retrying it.
            current, now = await self._read(operation_id)
            require_live(
                current, owner_id=record.owner_id, fencing_token=record.fencing_token, now=now
            )
            # Valid at this observation only, not guaranteed live after return.
            return current

        if previous.fencing_token == MAX_FENCING_TOKEN:
            raise InvalidOwnershipRequest("Fencing token space exhausted; never reset the record")
        predicate = ownership_to_document(previous)
        predicate["$expr"] = {
            "$and": [
                {"$gte": ["$$NOW", "$updated_at"]},
                {
                    "$or": [
                        {"$eq": ["$state", OwnershipState.RELEASED.value]},
                        {"$lte": ["$lease_expires_at", "$$NOW"]},
                    ]
                },
            ]
        }
        document = await collection.find_one_and_update(
            predicate,
            [
                {
                    "$set": {
                        "attempt_id": {"$literal": str(attempt_id)},
                        "owner_id": {"$literal": owner_id},
                        "fencing_token": {"$add": ["$fencing_token", 1]},
                        "state": OwnershipState.ACTIVE.value,
                        "acquired_at": "$$NOW",
                        "updated_at": "$$NOW",
                        "lease_expires_at": {"$add": ["$$NOW", lease_ms]},
                        "released_at": None,
                    }
                }
            ],
            upsert=False,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise OwnershipConflict(operation_id)
        return ownership_from_document(document)

    async def _change(
        self,
        operation_id: UUID,
        *,
        owner_id: str,
        fencing_token: int,
        lease_ms: int | None,
    ) -> OwnershipRecord:
        validate_key(operation_id, owner_id, fencing_token)
        if lease_ms is not None:
            validate_duration(lease_ms)
        previous, now = await self._read(operation_id)
        require_live(previous, owner_id=owner_id, fencing_token=fencing_token, now=now)
        predicate = ownership_to_document(previous)
        predicate["$expr"] = {
            "$and": [
                {"$gt": ["$lease_expires_at", "$$NOW"]},
                {"$gte": ["$$NOW", "$updated_at"]},
            ]
        }
        updates: dict[str, Any] = {"updated_at": "$$NOW"}
        if lease_ms is None:
            updates.update(state=OwnershipState.RELEASED.value, released_at="$$NOW")
        else:
            updates["lease_expires_at"] = {
                "$max": [
                    "$lease_expires_at",
                    {"$add": ["$$NOW", lease_ms]},
                ]
            }
        collection = await self._collection()
        document = await collection.find_one_and_update(
            predicate,
            [{"$set": updates}],
            upsert=False,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            await self._failed_change(operation_id, owner_id, fencing_token)
        return ownership_from_document(document)

    async def _failed_change(self, operation_id: UUID, owner_id: str, token: int) -> NoReturn:
        # Diagnostic read only. No retry, and it never grants ownership.
        current, now = await self._read(operation_id)
        require_live(current, owner_id=owner_id, fencing_token=token, now=now)
        raise OwnershipConflict("Ownership changed concurrently; inspect before a new request")

    async def renew(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int, lease_ms: int
    ) -> OwnershipRecord:
        # Validate before dispatch: None must not accidentally select release.
        validate_duration(lease_ms)
        return await self._change(
            operation_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            lease_ms=lease_ms,
        )

    async def release(
        self, operation_id: UUID, *, owner_id: str, fencing_token: int
    ) -> OwnershipRecord:
        return await self._change(
            operation_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            lease_ms=None,
        )
