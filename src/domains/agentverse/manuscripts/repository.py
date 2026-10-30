"""``coll_manuscript_sessions`` on the DI-owned MongoDB infrastructure.

Identity is the built-in unique ``_id`` (the stable session ID). Every change replaces
the whole session document only from its exact previous revision and state digest
(compare-and-set), so concurrent transitions cannot both apply and a crash leaves either
the previous or the next complete state, never a partial one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.domains.agentverse.manuscripts.sessions import (
    SESSIONS_COLLECTION,
    ManuscriptSession,
    SessionCorrupt,
    SessionUnavailable,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository


def to_document(session: ManuscriptSession) -> dict[str, Any]:
    return {"_id": session.session_id, **session.model_dump(mode="json")}


def from_document(document: dict[str, Any]) -> ManuscriptSession:
    try:
        data = dict(document)
        identity = data.pop("_id")
        session = ManuscriptSession.model_validate(data)
        if identity != session.session_id:
            raise ValueError("Storage and session identities disagree")
        return session
    except (ValueError, TypeError, KeyError) as exc:
        raise SessionCorrupt("Invalid stored Manuscript session") from exc


class MongoManuscriptSessionRepository:
    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = SESSIONS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection_name = collection_name

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection_name)
        except PyMongoError as exc:
            raise SessionUnavailable("Manuscript session store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise SessionUnavailable("Manuscript sessions require acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def insert(self, session: ManuscriptSession) -> tuple[ManuscriptSession, bool]:
        collection = await self._collection()
        try:
            await collection.insert_one(to_document(session))
            return session, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise SessionUnavailable("Session write outcome is unknown") from exc
        stored = await self.get(session.tenant_id, session.session_id)
        if stored is None:
            raise SessionUnavailable("Session write outcome is unknown")
        return stored, False

    async def get(self, tenant_id: str, session_id: str) -> ManuscriptSession | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": session_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise SessionUnavailable("Manuscript session store is unavailable") from exc
        return None if document is None else from_document(document)

    async def replace(self, previous: ManuscriptSession, following: ManuscriptSession) -> bool:
        """Compare-and-set ``previous`` → ``following``; ``False`` if another write won."""
        if (
            following.session_id != previous.session_id
            or following.tenant_id != previous.tenant_id
            or following.revision != previous.revision + 1
        ):
            raise ValueError("A transition advances exactly one revision of one session")
        collection = await self._collection()
        try:
            result = await collection.replace_one(
                {
                    "_id": previous.session_id,
                    "tenant_id": previous.tenant_id,
                    "revision": previous.revision,
                    "state_digest": previous.state_digest,
                },
                to_document(following),
            )
        except PyMongoError as exc:
            raise SessionUnavailable("Session transition outcome is unknown") from exc
        return bool(result.matched_count)
