"""Activity context persistence on the DI-owned MongoDB infrastructure.

Two collections, both keyed only by ``_id`` and changed only by single-document
compare-and-set, so the guarantees are the same on MongoDB and the Cosmos DB Mongo API
(no secondary unique or partial index, no multi-document transaction, no sort on an
unindexed field):

- ``coll_activity_context_heads``: one head per scope (``_id = scope_key``). The head's
  ``active_context_id`` is the only authority for the active generation.
- ``coll_activity_contexts``: one immutable-identity document per generation
  (``_id = context_id``).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.domains.agentverse.activities.models import (
    CONTEXTS_COLLECTION,
    HEADS_COLLECTION,
    MAX_GENERATIONS,
    ActivityContext,
    ActivityCorrupt,
    ActivityScopeHead,
    ActivityUnavailable,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository


def _json(data: dict[str, Any]) -> str:
    def encode(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    return json.dumps(data, default=encode)


def _load(model: Any, document: dict[str, Any], identity_field: str) -> Any:
    try:
        data = dict(document)
        identity = data.pop("_id")
        record = model.model_validate_json(_json(data))
        if identity != getattr(record, identity_field):
            raise ValueError("Storage and record identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise ActivityCorrupt("Invalid stored activity record") from exc


class MongoActivityContextRepository:
    def __init__(
        self,
        mongodb_repository: MongoDBRepository,
        contexts_collection: str = CONTEXTS_COLLECTION,
        heads_collection: str = HEADS_COLLECTION,
    ) -> None:
        self.repository = mongodb_repository
        self.contexts_collection = contexts_collection
        self.heads_collection = heads_collection

    async def _collection(self, name: str) -> Any:
        try:
            collection = await self.repository.get_collection(name)
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise ActivityUnavailable("Activity contexts require acknowledged writes")
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    # --- Heads ----------------------------------------------------------------------------

    async def head(self, tenant_id: str, scope_key: str) -> ActivityScopeHead | None:
        collection = await self._collection(self.heads_collection)
        try:
            document = await collection.find_one({"_id": scope_key, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity store is unavailable") from exc
        return None if document is None else _load(ActivityScopeHead, document, "scope_key")

    async def create_head(self, head: ActivityScopeHead) -> ActivityScopeHead:
        """Insert the empty head once; a concurrent creator's head is returned instead."""
        collection = await self._collection(self.heads_collection)
        try:
            await collection.insert_one({"_id": head.scope_key, **head.model_dump(mode="json")})
            return head
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity head write outcome is unknown") from exc
        stored = await self.head(head.tenant_id, head.scope_key)
        if stored is None:
            raise ActivityUnavailable("Activity head write outcome is unknown")
        return stored

    async def replace_head(self, previous: ActivityScopeHead, following: ActivityScopeHead) -> bool:
        if (
            following.scope_key != previous.scope_key
            or following.tenant_id != previous.tenant_id
            or following.revision != previous.revision + 1
        ):
            raise ValueError("A head transition advances exactly one revision")
        collection = await self._collection(self.heads_collection)
        try:
            result = await collection.replace_one(
                {
                    "_id": previous.scope_key,
                    "tenant_id": previous.tenant_id,
                    "revision": previous.revision,
                    "state_digest": previous.state_digest,
                },
                {"_id": following.scope_key, **following.model_dump(mode="json")},
            )
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity head transition outcome is unknown") from exc
        return bool(result.matched_count)

    # --- Contexts -------------------------------------------------------------------------

    async def insert(self, context: ActivityContext) -> tuple[ActivityContext, bool]:
        collection = await self._collection(self.contexts_collection)
        try:
            await collection.insert_one(
                {"_id": context.context_id, **context.model_dump(mode="json")}
            )
            return context, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise ActivityUnavailable("Context write outcome is unknown") from exc
        stored = await self.get(context.tenant_id, context.context_id)
        if stored is None:
            raise ActivityUnavailable("Context write outcome is unknown")
        return stored, False

    async def get(self, tenant_id: str, context_id: str) -> ActivityContext | None:
        collection = await self._collection(self.contexts_collection)
        try:
            document = await collection.find_one({"_id": context_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity store is unavailable") from exc
        return None if document is None else _load(ActivityContext, document, "context_id")

    async def get_many(self, tenant_id: str, context_ids: list[str]) -> list[ActivityContext]:
        """By ``_id`` only (no secondary index); unordered, at most ``MAX_GENERATIONS``."""
        if len(context_ids) > MAX_GENERATIONS:
            raise ValueError("Too many contexts requested")
        collection = await self._collection(self.contexts_collection)
        try:
            documents = await collection.find(
                {"_id": {"$in": list(context_ids)}, "tenant_id": tenant_id}
            ).to_list(MAX_GENERATIONS)
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity store is unavailable") from exc
        return [_load(ActivityContext, document, "context_id") for document in documents]

    async def replace(self, previous: ActivityContext, following: ActivityContext) -> bool:
        """Compare-and-set a transition (``revision`` + 1) or an evidence settlement (same
        revision, fewer pending obligations); ``False`` if another write won."""
        same = following.revision == previous.revision
        if (
            following.context_id != previous.context_id
            or following.tenant_id != previous.tenant_id
            or following.revision not in (previous.revision, previous.revision + 1)
            or (
                same
                and (
                    following.model_dump(exclude={"pending_evidence", "state_digest"})
                    != previous.model_dump(exclude={"pending_evidence", "state_digest"})
                    or not {e.event_id for e in following.pending_evidence}
                    < {e.event_id for e in previous.pending_evidence}
                )
            )
        ):
            raise ValueError("Invalid context transition")
        collection = await self._collection(self.contexts_collection)
        try:
            result = await collection.replace_one(
                {
                    "_id": previous.context_id,
                    "tenant_id": previous.tenant_id,
                    "revision": previous.revision,
                    "state_digest": previous.state_digest,
                },
                {"_id": following.context_id, **following.model_dump(mode="json")},
            )
        except PyMongoError as exc:
            raise ActivityUnavailable("Context transition outcome is unknown") from exc
        return bool(result.matched_count)

    async def with_pending_evidence(self, tenant_id: str, limit: int = 50) -> list[ActivityContext]:
        """Read-only discovery of undelivered evidence (operators/Nataraja). Scans; no
        secondary index is required, so it is for bounded inspection, not hot paths."""
        collection = await self._collection(self.contexts_collection)
        try:
            documents = await collection.find(
                {"tenant_id": tenant_id, "pending_evidence.0": {"$exists": True}}
            ).to_list(min(max(limit, 1), 200))
        except PyMongoError as exc:
            raise ActivityUnavailable("Activity store is unavailable") from exc
        return [_load(ActivityContext, document, "context_id") for document in documents]
