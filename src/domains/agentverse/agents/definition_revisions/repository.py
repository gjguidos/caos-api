"""Insert-only Agent definition revision persistence (REV-1).

``coll_agent_definition_revisions``, keyed only by ``_id = revision_id(tenant, Agent,
revision)``. The only primitives are ``insert_one`` and ``find_one`` by ``_id`` (MongoDB /
Cosmos DB Mongo API parity; Cosmos vCore is untested). There is no update, replace,
delete, upsert, secondary index or transaction: a stored revision can never change, and
``_id`` uniqueness decides between competing successors for the same revision number.

Documents are stored as their JSON serialization, so a record reads back byte-identical
(no driver datetime or precision drift). This repository is not wired into the runtime
in REV-1: nothing writes revisions outside tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.domains.agentverse.agents.definition_revisions.models import (
    MAX_REVISION,
    REVISIONS_COLLECTION,
    AgentDefinitionRevision,
    RevisionConflict,
    RevisionCorrupt,
    RevisionPredecessorMismatch,
    RevisionUnavailable,
    revision_id,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository


class MongoAgentDefinitionRevisionRepository:
    def __init__(
        self, mongodb_repository: MongoDBRepository, collection: str = REVISIONS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection)
        except PyMongoError as exc:
            raise RevisionUnavailable("Revision store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise RevisionUnavailable("Revision records require acknowledged writes")
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    async def get(
        self, tenant_id: str, agent_id: str, revision: int
    ) -> AgentDefinitionRevision | None:
        """Exact-version lookup; a stored record for another identity is corruption."""
        collection = await self._collection()
        try:
            document = await collection.find_one(
                {"_id": revision_id(tenant_id, agent_id, revision)}
            )
        except PyMongoError as exc:
            raise RevisionUnavailable("Revision store is unavailable") from exc
        if document is None:
            return None
        record = _load(document)
        if (record.tenant_id, record.agent_id, record.revision) != (tenant_id, agent_id, revision):
            raise RevisionCorrupt("Stored revision identity mismatch")
        return record

    async def insert(self, record: AgentDefinitionRevision) -> tuple[AgentDefinitionRevision, bool]:
        """Insert once: ``(record, True)``; an identical retry returns ``(stored, False)``.

        A later revision requires its exact predecessor to be stored with the expected
        digest (records are immutable, so the check cannot race). A different record
        under the same identity is ``RevisionConflict``: the competing successor loses.
        """
        if record.predecessor is not None:
            previous = await self.get(record.tenant_id, record.agent_id, record.revision - 1)
            if previous is None or previous.revision_digest != record.predecessor.revision_digest:
                raise RevisionPredecessorMismatch("The exact predecessor revision is not stored")
        collection = await self._collection()
        document = {"_id": record.revision_id, **json.loads(record.model_dump_json())}
        try:
            await collection.insert_one(document)
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise RevisionUnavailable("Revision insertion outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.agent_id, record.revision)
        if stored is None:
            raise RevisionUnavailable("Revision insertion outcome is unknown")
        if stored.content() != record.content():
            raise RevisionConflict("Another definition holds this revision")
        return stored, False

    async def chain(self, tenant_id: str, agent_id: str) -> list[AgentDefinitionRevision]:
        """The contiguous history from revision 1, each link verified against its
        predecessor (exact ``_id`` lookups only; bounded)."""
        history: list[AgentDefinitionRevision] = []
        for revision in range(1, MAX_REVISION + 1):
            record = await self.get(tenant_id, agent_id, revision)
            if record is None:
                return history
            if history and (
                record.predecessor is None
                or record.predecessor.revision_digest != history[-1].revision_digest
            ):
                raise RevisionCorrupt("Revision chain is broken")
            history.append(record)
        return history


def _load(document: dict[str, Any]) -> AgentDefinitionRevision:
    try:
        data = dict(document)
        identity = data.pop("_id")
        record = AgentDefinitionRevision.model_validate_json(json.dumps(data))
    except (ValueError, TypeError, KeyError) as exc:
        raise RevisionCorrupt("Invalid stored revision record") from exc
    if identity != record.revision_id:
        raise RevisionCorrupt("Storage and record identities disagree")
    return record
