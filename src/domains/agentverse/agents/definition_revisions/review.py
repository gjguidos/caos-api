"""Immutable, digest-bound Agent revision reviews (REV-2, ADR-0005).

A review records what one exact draft revision would change, against one exact
predecessor, under the authority and validation current when it was produced. It is
**not** an approval (REV-3) and **not** an application (REV-4): it authorizes nothing.
A review is usable only while it is current: unexpired, its draft revision still the
draft's latest, and its predecessor still the Agent's current definition. Anything else
fails closed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

REVIEW_CONTRACT: Final = "caos.agent_definition_revision_review"
REVIEWS_COLLECTION: Final = "coll_agent_definition_revision_reviews"
ReviewState = Literal[
    "current",
    "ineligible",
    "expired",
    "stale_candidate",
    "stale_predecessor",
    "stale_authority",
    "unavailable",
]


class RevisionReviewError(Exception):
    code = "revision_review.error"
    retryable = False


class RevisionReviewNotFound(RevisionReviewError):
    code = "revision_review.not_found"


class RevisionReviewNotCurrent(RevisionReviewError):
    """Expired, stale or ineligible: never usable by a later step."""

    def __init__(self, state: str) -> None:
        super().__init__("The review is not current")
        self.code = f"revision_review.{state}"
        self.state = state


class RevisionReviewUnavailable(RevisionReviewError):
    code = "revision_review.unavailable"
    retryable = True


class RevisionReviewCorrupt(RevisionReviewError):
    code = "revision_review.corrupt"


def review_id(review_digest: str | None, snapshot_digest: str, validated_at: datetime) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-definition-revision-review:"
            + sha256_hex(
                {
                    "review_digest": review_digest,
                    "snapshot": snapshot_digest,
                    "at": validated_at.isoformat(),
                }
            ),
        )
    )


class RevisionReview(BaseModel):
    """Durable review. ``snapshot`` is internal and bound by ``review_digest`` when
    eligible; ``envelope`` is the visibility-safe requester projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_definition_revision_review"] = REVIEW_CONTRACT
    schema_version: Literal[1] = 1
    review_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    executor_id: str = Field(min_length=1, max_length=128)
    draft_id: str = Field(min_length=1, max_length=64)
    draft_revision: int = Field(ge=1)
    predecessor_revision_digest: Digest
    candidate_definition_digest: Digest | None
    eligible: StrictBool
    review_digest: Digest | None
    snapshot_digest: Digest
    snapshot: dict[str, Any]
    envelope: dict[str, Any]
    authorization: AuthorizationProvenance
    validated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if sha256_hex(self.snapshot) != self.snapshot_digest:
            raise ValueError("Review snapshot does not match its digest")
        if self.eligible != (self.review_digest is not None):
            raise ValueError("Exactly an eligible review is digest-bound")
        if self.review_digest is not None and self.review_digest != self.snapshot_digest:
            raise ValueError("Review digest differs from its snapshot")
        if self.review_id != review_id(self.review_digest, self.snapshot_digest, self.validated_at):
            raise ValueError("Review identity mismatch")
        snapshot = self.snapshot
        if (
            snapshot.get("tenant_id"),
            (snapshot.get("requester") or {}).get("subject_id"),
            snapshot.get("agent_id"),
            snapshot.get("executor_id"),
            (snapshot.get("draft") or {}).get("draft_id"),
            (snapshot.get("draft") or {}).get("revision"),
            (snapshot.get("predecessor") or {}).get("revision_digest"),
        ) != (
            self.tenant_id,
            self.subject_id,
            self.agent_id,
            self.executor_id,
            self.draft_id,
            self.draft_revision,
            self.predecessor_revision_digest,
        ):
            raise ValueError("Review identity differs from its snapshot")
        if (self.authorization.tenant_id, self.authorization.subject_id) != (
            self.tenant_id,
            self.subject_id,
        ):
            raise ValueError("Review provenance differs from its requester")
        if self.expires_at <= self.validated_at:
            raise ValueError("A review expires after it is produced")
        return self


class MongoRevisionReviewRepository:
    """Insert-only reviews keyed by ``_id = review_id``."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection: str = REVIEWS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection)
        except PyMongoError as exc:
            raise RevisionReviewUnavailable("Revision review store is unavailable") from exc
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    async def insert(self, review: RevisionReview) -> RevisionReview:
        collection = await self._collection()
        document = {"_id": review.review_id, **json.loads(review.model_dump_json())}
        try:
            await collection.insert_one(document)
            return review
        except DuplicateKeyError:
            stored = await self.get(review.tenant_id, review.review_id)
            if stored is None or stored.model_dump() != review.model_dump():
                raise RevisionReviewCorrupt("Review identity collision") from None
            return stored
        except PyMongoError as exc:
            raise RevisionReviewUnavailable("Review write outcome is unknown") from exc

    async def get(self, tenant_id: str, review_id_: str) -> RevisionReview | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": review_id_})
        except PyMongoError as exc:
            raise RevisionReviewUnavailable("Revision review store is unavailable") from exc
        if document is None:
            return None
        try:
            data = dict(document)
            data.pop("_id")
            record = RevisionReview.model_validate_json(json.dumps(data))
        except (ValueError, TypeError) as exc:
            raise RevisionReviewCorrupt("Invalid stored revision review") from exc
        if record.tenant_id != tenant_id:
            return None  # Another tenant's review is indistinguishable from a missing one.
        return record
