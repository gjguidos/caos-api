"""Mongo snapshot adapter using CAOS's DI-owned database infrastructure."""

import json
from typing import Any
from uuid import UUID

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError

from src.base.operations.v2.models import OperationAttempt, OperationRecord
from src.base.operations.v2.repository import (
    CorruptSnapshot,
    DuplicateOperation,
    OperationNotFound,
    RevisionConflict,
    validate_revision,
    validate_snapshot,
    validate_successor,
)
from src.base.repositories.mongodb_repository import MongoDBRepository

COLLECTION_NAME = "operations_v2"
SCHEMA_VERSION = 1


def snapshot_to_document(operation: OperationRecord) -> dict[str, Any]:
    operation = validate_snapshot(operation)
    return {
        "_id": str(operation.operation_id),
        "schema_version": SCHEMA_VERSION,
        **operation.model_dump(mode="json"),
    }


def snapshot_from_document(document: dict[str, Any]) -> OperationRecord:
    try:
        data = dict(document)
        identity = data.pop("_id")
        version = data.pop("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("Unsupported storage schema version")
        # All fields are serialized explicitly, even nullable/default fields.
        if set(data) != set(OperationRecord.model_fields):
            raise ValueError("Incomplete or unknown snapshot fields")
        if not isinstance(data["attempts"], list) or any(
            not isinstance(attempt, dict) or set(attempt) != set(OperationAttempt.model_fields)
            for attempt in data["attempts"]
        ):
            raise ValueError("Incomplete or unknown attempt fields")
        operation = OperationRecord.model_validate_json(json.dumps(data), strict=True)
        if identity != str(operation.operation_id):
            raise ValueError("Storage and snapshot identities disagree")
        return operation
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptSnapshot("Invalid stored operation snapshot") from exc


class MongoOperationRepository:
    """One document per operation, unique built-in _id index, no TTL/upsert.

    Inherits acknowledged write concern from the configured CAOS deployment.
    Does not own the client lifecycle or retry database failures.
    """

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = COLLECTION_NAME
    ) -> None:
        self._mongodb_repository = mongodb_repository
        self._collection_name = collection_name

    async def _collection(self) -> Any:
        collection = await self._mongodb_repository.get_collection(self._collection_name)
        if not collection.write_concern.acknowledged:
            raise ValueError("Operation persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def create(self, operation: OperationRecord) -> None:
        document = snapshot_to_document(operation)
        collection = await self._collection()
        try:
            await collection.insert_one(document)
        except DuplicateKeyError as exc:
            raise DuplicateOperation(operation.operation_id) from exc

    async def get(self, operation_id: UUID) -> OperationRecord:
        collection = await self._collection()
        document = await collection.find_one({"_id": str(operation_id)})
        if document is None:
            raise OperationNotFound(operation_id)
        return snapshot_from_document(document)

    async def commit(self, operation: OperationRecord, *, expected_revision: int) -> None:
        operation = validate_snapshot(operation)
        validate_revision(operation, expected_revision)
        previous = await self.get(operation.operation_id)
        if previous.revision != expected_revision:
            raise RevisionConflict(operation.operation_id)
        validate_successor(previous, operation)
        collection = await self._collection()
        result = await collection.replace_one(
            {"_id": str(operation.operation_id), "revision": expected_revision},
            snapshot_to_document(operation),
            upsert=False,
        )
        if result.matched_count == 0:
            # No delete API exists here. This read distinguishes an externally
            # removed document from a competing commit; it is not a retry.
            await self.get(operation.operation_id)
            raise RevisionConflict(operation.operation_id)
