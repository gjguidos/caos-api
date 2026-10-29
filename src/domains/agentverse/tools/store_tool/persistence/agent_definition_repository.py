"""Insert-only, idempotent agent definition persistence in the existing ``coll_agents``.

One ``insert_one`` guarded by a unique partial index on the store idempotency scope is
the atomic create-if-absent gate; there is no upsert, update or replace, so an existing
agent can never be modified here. The only pre-write read is the policy-pinned ID check. Legacy and V1 documents carry no scope
and are therefore unconstrained by the index. Requires native MongoDB partial indexes;
Cosmos Mongo API compatibility is unverified and fails closed at index creation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.tools.store_tool.models import (
    AmbiguousStoreWrite,
    StoreConflict,
    StoreUnavailable,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

STORE_FIELD = "store"
SCOPE_FIELD = f"{STORE_FIELD}.idempotency_scope"
IDEMPOTENCY_INDEX = "uq_agent_store_idempotency"


class AgentDefinitionRepository:
    def __init__(
        self,
        repository: MongoDBRepository,
        collection_name: str = DBService.AGENTS_COL,
    ) -> None:
        self.repository = repository
        self.collection_name = collection_name
        self._indexed = False

    async def ensure_indexes(self) -> None:
        """Idempotent; a conflicting or unsupported index definition fails closed."""
        collection = await self.repository.get_collection(self.collection_name)
        try:
            await collection.create_index(
                [(SCOPE_FIELD, ASCENDING)],
                name=IDEMPOTENCY_INDEX,
                unique=True,
                partialFilterExpression={SCOPE_FIELD: {"$exists": True}},
            )
        except PyMongoError as exc:
            raise StoreUnavailable("Agent store idempotency index is unavailable") from exc
        self._indexed = True

    async def find_by_id(self, agent_id: str) -> dict[str, Any] | None:
        """Read-only identity lookup used before writing a policy-pinned agent ID."""
        collection = await self.repository.get_collection(self.collection_name)
        try:
            return await collection.find_one({"id": agent_id}, {"_id": 0})
        except PyMongoError as exc:
            raise StoreUnavailable("Agent store is unavailable") from exc

    async def insert_once(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert, or return the document already holding this scope: ``(doc, created)``.

        The caller compares request digests; this layer only enforces the atomic gate.
        """
        scope = document[STORE_FIELD]["idempotency_scope"]
        if not self._indexed:
            # Scoped idempotency depends on the unique index; never insert without it.
            await self.ensure_indexes()
        collection = await self.repository.get_collection(self.collection_name)
        try:
            await collection.insert_one(dict(document))
            return document, True
        except DuplicateKeyError as exc:
            if SCOPE_FIELD not in ((exc.details or {}).get("keyPattern") or {}):
                raise StoreConflict("Agent identity already exists") from exc
        except PyMongoError as exc:
            raise AmbiguousStoreWrite("Agent write outcome is unknown") from exc
        try:
            existing = await collection.find_one({SCOPE_FIELD: scope}, {"_id": 0})
        except PyMongoError as exc:
            raise AmbiguousStoreWrite("Agent write outcome is unknown") from exc
        if existing is None:
            # The conflicting insert is not yet visible to this read; no blind retry here.
            raise AmbiguousStoreWrite("Agent write outcome is unknown")
        return existing, False
