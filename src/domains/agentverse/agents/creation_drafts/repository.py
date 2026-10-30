"""Draft persistence on the DI-owned MongoDB infrastructure.

``coll_agent_draft_revisions`` holds one immutable document per committed revision,
identified by ``_id = "<draft_id>:<revision>"``. Inserting revision ``n + 1`` is the
atomic compare-and-set: the built-in unique ``_id`` admits exactly one writer per
revision, so concurrent updates from the same expected revision cannot both commit
and nothing is ever overwritten. No secondary index is required (MongoDB and the
Cosmos DB Mongo API behave alike).

``coll_agent_drafts`` (``_id = draft_id``) is a projection of the latest committed
revision for single-document reads. It only moves forward, by a replace conditioned
on its previous revision, and any reader rolls a lagging head forward from the
revision log, so a crash between the two writes never loses or hides a commit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pymongo import DESCENDING, ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import canonical_json
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    REVISIONS_COLLECTION,
    AgentDraftRevision,
    CorruptDraft,
    DraftUnavailable,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository


def revision_key(draft_id: str, revision: int) -> str:
    return f"{draft_id}:{revision}"


LISTING_INDEX = "draft_owner_recent"


def sort_key(value: datetime) -> str:
    """Fixed-width UTC text, so lexicographic order is time order (storage only)."""
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def to_document(record: AgentDraftRevision, identity: str) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"content"})
    # Canonical JSON: Tool configuration may hold keys MongoDB cannot store as fields.
    return {
        "_id": identity,
        **data,
        "content_json": canonical_json(record.content),
        "sort_key": sort_key(record.committed_at),
    }


def from_document(document: dict[str, Any], identity: str) -> AgentDraftRevision:
    try:
        data = dict(document)
        data.pop("sort_key", None)
        if data.pop("_id") != identity:
            raise ValueError("Storage and draft identities disagree")
        data["content"] = json.loads(data.pop("content_json"))
        # Revalidation recomputes the content and revision digests.
        return AgentDraftRevision.model_validate(data)
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptDraft("Invalid stored draft") from exc


class MongoAgentDraftRepository:
    def __init__(
        self,
        mongodb_repository: MongoDBRepository,
        drafts_collection: str = DRAFTS_COLLECTION,
        revisions_collection: str = REVISIONS_COLLECTION,
    ) -> None:
        self.repository = mongodb_repository
        self.drafts_collection = drafts_collection
        self.revisions_collection = revisions_collection
        self._indexed = False

    async def _collection(self, name: str) -> Any:
        try:
            collection = await self.repository.get_collection(name)
        except PyMongoError as exc:
            raise DraftUnavailable("Draft store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise DraftUnavailable("Draft persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def commit(self, record: AgentDraftRevision) -> tuple[AgentDraftRevision, bool]:
        """Insert one immutable revision: ``(stored, committed)``.

        ``committed`` is false when that revision already exists; the stored one is
        returned and the caller decides between replay and conflict.
        """
        revisions = await self._collection(self.revisions_collection)
        identity = revision_key(record.draft_id, record.revision)
        try:
            await revisions.insert_one(to_document(record, identity))
        except DuplicateKeyError:
            stored = await self.revision(record.tenant_id, record.draft_id, record.revision)
            if stored is None:
                raise DraftUnavailable("Draft write outcome is unknown") from None
            return stored, False
        except PyMongoError as exc:
            raise DraftUnavailable("Draft write outcome is unknown") from exc
        try:
            await self._advance(record)
        except DraftUnavailable:
            pass  # Committed: the revision log is authoritative and reads roll forward.
        return record, True

    async def list_owned(
        self,
        tenant_id: str,
        owner_subject_id: str,
        source_agent_id: str,
        *,
        statuses: tuple[str, ...] | None,
        limit: int,
        after: tuple[str, str] | None,
    ) -> tuple[list[AgentDraftRevision], tuple[str, str] | None]:
        """One page of the owner's drafts for a source, newest first, from the
        projection (callers roll each forward). Returns the page and the next position."""
        drafts = await self._collection(self.drafts_collection)
        if not self._indexed:
            try:
                await drafts.create_index(
                    [
                        ("tenant_id", 1),
                        ("owner_subject_id", 1),
                        ("source_agent_id", 1),
                        ("sort_key", DESCENDING),
                        ("_id", DESCENDING),
                    ],
                    name=LISTING_INDEX,
                )
            except PyMongoError as exc:
                raise DraftUnavailable("Draft listing index is unavailable") from exc
            self._indexed = True
        query: dict[str, Any] = {
            "tenant_id": tenant_id,
            "owner_subject_id": owner_subject_id,
            "source_agent_id": source_agent_id,
        }
        if statuses:
            query["status"] = {"$in": list(statuses)}
        if after is not None:
            query["$or"] = [
                {"sort_key": {"$lt": after[0]}},
                {"sort_key": after[0], "_id": {"$lt": after[1]}},
            ]
        try:
            documents = (
                await drafts.find(query)
                .sort([("sort_key", DESCENDING), ("_id", DESCENDING)])
                .limit(limit + 1)
                .to_list(limit + 1)
            )
        except PyMongoError as exc:
            raise DraftUnavailable("Draft store is unavailable") from exc
        page = documents[:limit]
        following = (page[-1]["sort_key"], page[-1]["_id"]) if len(documents) > limit else None
        return [from_document(document, document["_id"]) for document in page], following

    async def revision(
        self, tenant_id: str, draft_id: str, revision: int
    ) -> AgentDraftRevision | None:
        revisions = await self._collection(self.revisions_collection)
        identity = revision_key(draft_id, revision)
        try:
            document = await revisions.find_one({"_id": identity, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise DraftUnavailable("Draft store is unavailable") from exc
        return None if document is None else from_document(document, identity)

    async def current(self, tenant_id: str, draft_id: str) -> AgentDraftRevision | None:
        """The latest committed revision, rolling a lagging projection forward."""
        drafts = await self._collection(self.drafts_collection)
        try:
            document = await drafts.find_one({"_id": draft_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise DraftUnavailable("Draft store is unavailable") from exc
        head = None if document is None else from_document(document, draft_id)
        while True:
            following = await self.revision(
                tenant_id, draft_id, 1 if head is None else head.revision + 1
            )
            if following is None:
                return head
            if head is not None and following.parent_digest != head.revision_digest:
                raise CorruptDraft("Draft revision chain is broken")
            await self._advance(following)
            head = following

    async def _advance(self, record: AgentDraftRevision) -> None:
        """Move the projection to ``record`` only from its parent revision (idempotent)."""
        drafts = await self._collection(self.drafts_collection)
        document = to_document(record, record.draft_id)
        try:
            if record.revision == 1:
                await drafts.insert_one(document)
            else:
                await drafts.replace_one(
                    {
                        "_id": record.draft_id,
                        "tenant_id": record.tenant_id,
                        "revision": record.revision - 1,
                        "revision_digest": record.parent_digest,
                    },
                    document,
                )
        except DuplicateKeyError:
            pass  # Already projected; the head never moves backward.
        except PyMongoError as exc:
            # The revision is committed; the next read rolls the projection forward.
            raise DraftUnavailable("Draft projection outcome is unknown") from exc
