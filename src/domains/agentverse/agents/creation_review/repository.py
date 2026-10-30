"""``coll_agent_creation_review_approvals`` on the DI-owned MongoDB infrastructure.

Identity is the built-in unique ``_id`` (the stable approval ID), so idempotency needs
no secondary unique index and behaves the same on MongoDB and the Cosmos DB Mongo API.
The reviewed snapshot is stored once as canonical JSON and is never updated; state
changes are compare-and-set transitions guarded by the current status.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import canonical_json
from src.domains.agentverse.agents.creation_review.models import (
    APPROVALS_COLLECTION,
    ApprovalStatus,
    ApprovalUnavailable,
    CorruptApproval,
    DraftReviewApproval,
    IdempotencyConflict,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

_MUTABLE = frozenset(
    {
        "status",
        "approved_at",
        "approval_event_id",
        "revoked_at",
        "revocation_authorization",
        "revocation_event_id",
        "consumed_by",
    }
)


def to_document(record: DraftReviewApproval) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"snapshot"})
    return {"_id": record.approval_id, **data, "snapshot_json": canonical_json(record.snapshot)}


def from_document(document: dict[str, Any]) -> DraftReviewApproval:
    try:
        data = dict(document)
        identity = data.pop("_id")
        data["snapshot"] = json.loads(data.pop("snapshot_json"))
        record = DraftReviewApproval.model_validate(data)
        if identity != record.approval_id:
            raise ValueError("Storage and approval identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptApproval("Invalid stored approval") from exc


class MongoDraftReviewApprovalRepository:
    def __init__(
        self,
        mongodb_repository: MongoDBRepository,
        collection_name: str = APPROVALS_COLLECTION,
    ) -> None:
        self.repository = mongodb_repository
        self.collection_name = collection_name

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection_name)
        except PyMongoError as exc:
            raise ApprovalUnavailable("Approval store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise ApprovalUnavailable("Approval persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def insert(self, record: DraftReviewApproval) -> tuple[DraftReviewApproval, bool]:
        """Insert once. A replay returns the stored record; another request under the
        same identity is an idempotency conflict, never an overwrite."""
        document = to_document(record)
        collection = await self._collection()
        try:
            await collection.insert_one(dict(document))
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise ApprovalUnavailable("Approval write outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.approval_id)
        if stored is None:
            raise ApprovalUnavailable("Approval write outcome is unknown")
        if stored.request_digest != record.request_digest:
            raise IdempotencyConflict("Idempotency key already used for another request")
        return stored, False

    async def get(self, tenant_id: str, approval_id: str) -> DraftReviewApproval | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": approval_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise ApprovalUnavailable("Approval store is unavailable") from exc
        return None if document is None else from_document(document)

    async def transition(
        self,
        record: DraftReviewApproval,
        *,
        expected: tuple[ApprovalStatus, ...],
        changes: dict[str, Any],
        unset: tuple[str, ...] = (),
    ) -> DraftReviewApproval:
        """Compare-and-set from one of ``expected``; returns the current stored record.

        Concurrent transitions resolve to exactly one winner; the caller inspects the
        returned state instead of assuming its own change applied. ``unset`` fields must
        still be empty, so recorded evidence and provenance are written at most once.
        """
        if not changes or not set(changes) <= _MUTABLE:
            raise ValueError("Only approval state fields can change")
        updated = record.model_copy(update=changes)
        # Revalidate the full record so an invalid state is never written.
        DraftReviewApproval.model_validate(updated.model_dump())
        values = {key: to_document(updated)[key] for key in changes}
        collection = await self._collection()
        try:
            await collection.update_one(
                {
                    "_id": record.approval_id,
                    "tenant_id": record.tenant_id,
                    "review_digest": record.review_digest,
                    "status": {"$in": list(expected)},
                    **{field: None for field in unset},
                },
                {"$set": values},
            )
        except PyMongoError as exc:
            raise ApprovalUnavailable("Approval transition outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.approval_id)
        if stored is None:
            raise CorruptApproval("Approval disappeared during a transition")
        return stored
