"""Append-only ``coll_logs`` persistence on the DI-owned MongoDB infrastructure.

Identity is the built-in unique ``_id`` (a tenant-scoped digest of the event ID), so
idempotency needs no partial or unique secondary index; this keeps the correctness
guarantee identical on MongoDB and the Cosmos DB Mongo API. There is no update,
replace, upsert, delete or TTL path: a stored record can never be changed here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.domains.agentverse.audit.models import LOGS_COLLECTION, CAOSLogRecord, storage_id

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

CORRELATION_INDEX = "ix_logs_tenant_correlation"
MAX_LIST_LIMIT = 500


class LogError(Exception):
    """Log persistence failure; messages never contain record content."""

    code = "LOG_ERROR"
    retryable = False


class LogConflict(LogError):
    """The event ID was already recorded with different content in this tenant."""

    code = "LOG_CONFLICT"


class AmbiguousLogWrite(LogError):
    """The append may or may not have committed: repeat it with the same event ID."""

    code = "LOG_AMBIGUOUS_WRITE"
    retryable = True


class LogUnavailable(LogError):
    code = "LOG_UNAVAILABLE"
    retryable = True


class CorruptLogRecord(LogError):
    code = "LOG_CORRUPT_RECORD"


def record_from_document(document: dict[str, Any]) -> CAOSLogRecord:
    try:
        data = dict(document)
        identity = data.pop("_id")
        # Every field is stored explicitly; anything missing or extra is corruption.
        if set(data) != set(CAOSLogRecord.model_fields):
            raise ValueError("Incomplete or unknown log record fields")
        record = CAOSLogRecord.model_validate_json(json.dumps(data))
        if identity != storage_id(record.tenant_id, record.event_id):
            raise ValueError("Storage and record identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptLogRecord("Invalid stored log record") from exc


class MongoLogRepository:
    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = LOGS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection_name = collection_name

    async def _collection(self) -> Any:
        collection = await self.repository.get_collection(self.collection_name)
        if not collection.write_concern.acknowledged:
            raise LogUnavailable("Log persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def ensure_indexes(self) -> None:
        """Query support only (plain compound, no unique/partial/TTL options).
        Correctness never depends on it; failures surface as ``LogUnavailable``."""
        collection = await self._collection()
        try:
            await collection.create_index(
                [
                    ("tenant_id", ASCENDING),
                    ("correlation_id", ASCENDING),
                    ("recorded_at", ASCENDING),
                ],
                name=CORRELATION_INDEX,
            )
        except PyMongoError as exc:
            raise LogUnavailable("Log index is unavailable") from exc

    async def append(self, record: CAOSLogRecord) -> tuple[CAOSLogRecord, bool]:
        """Insert once. Returns ``(stored, created)``; an identical retry returns the
        original record unchanged, different content under the same ID conflicts."""
        document = record.to_document()
        collection = await self._collection()
        try:
            await collection.insert_one(dict(document))
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise AmbiguousLogWrite("Log write outcome is unknown") from exc
        try:
            existing = await collection.find_one({"_id": document["_id"]})
        except PyMongoError as exc:
            raise AmbiguousLogWrite("Log write outcome is unknown") from exc
        if existing is None:
            raise AmbiguousLogWrite("Log write outcome is unknown")
        stored = record_from_document(existing)
        if stored.content_digest != record.content_digest:
            raise LogConflict("Event ID already recorded with different content")
        return stored, False

    async def get(self, tenant_id: str, event_id: str) -> CAOSLogRecord | None:
        collection = await self._collection()
        try:
            document = await collection.find_one(
                {"_id": storage_id(tenant_id, event_id), "tenant_id": tenant_id}
            )
        except PyMongoError as exc:
            raise LogUnavailable("Log store is unavailable") from exc
        return None if document is None else record_from_document(document)

    async def list_by_correlation(
        self, tenant_id: str, correlation_id: str, *, limit: int = 100
    ) -> list[CAOSLogRecord]:
        """Tenant scope is mandatory; ordered by (recorded_at, event_id)."""
        if not 0 < limit <= MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        collection = await self._collection()
        try:
            # Sort only on indexed fields (required by Cosmos); recorded_at is fixed-width
            # UTC text, so its string order is time order. event_id breaks ties below.
            cursor = (
                collection.find({"tenant_id": tenant_id, "correlation_id": correlation_id})
                .sort("recorded_at", ASCENDING)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise LogUnavailable("Log store is unavailable") from exc
        records = [record_from_document(document) for document in documents]
        return sorted(records, key=lambda r: (r.recorded_at, r.event_id))
