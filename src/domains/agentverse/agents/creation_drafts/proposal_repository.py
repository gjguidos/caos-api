"""``coll_agent_draft_proposals`` on the DI-owned MongoDB infrastructure.

Identity is the built-in unique ``_id`` (the stable proposal ID), so idempotent
submission needs no secondary index. Proposed content is stored once as canonical JSON
and never updated; ``status``, ``claim`` and ``outcome`` change only by
compare-and-set on the current status and claim.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pymongo import DESCENDING, ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import canonical_json
from src.domains.agentverse.agents.creation_drafts.proposals import (
    PROPOSALS_COLLECTION,
    AgentDraftProposal,
    CorruptProposal,
    ProposalStatus,
    ProposalUnavailable,
)
from src.domains.agentverse.agents.creation_drafts.repository import sort_key

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

_MUTABLE = frozenset({"status", "claim", "outcome"})


LISTING_INDEX = "proposal_draft_recent"


def to_document(record: AgentDraftProposal) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"changes"})
    return {
        "_id": record.proposal_id,
        **data,
        "changes_json": canonical_json(record.changes),
        "sort_key": sort_key(record.created_at),
    }


def from_document(document: dict[str, Any]) -> AgentDraftProposal:
    try:
        data = dict(document)
        data.pop("sort_key", None)
        identity = data.pop("_id")
        data["changes"] = json.loads(data.pop("changes_json"))
        record = AgentDraftProposal.model_validate(data)
        if identity != record.proposal_id:
            raise ValueError("Storage and proposal identities disagree")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise CorruptProposal("Invalid stored proposal") from exc


class MongoAgentDraftProposalRepository:
    def __init__(
        self,
        mongodb_repository: MongoDBRepository,
        collection_name: str = PROPOSALS_COLLECTION,
    ) -> None:
        self.repository = mongodb_repository
        self.collection_name = collection_name
        self._indexed = False

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection_name)
        except PyMongoError as exc:
            raise ProposalUnavailable("Proposal store is unavailable") from exc
        if not collection.write_concern.acknowledged:
            raise ProposalUnavailable("Proposal persistence requires acknowledged writes")
        return collection.with_options(read_preference=ReadPreference.PRIMARY)

    async def insert(self, record: AgentDraftProposal) -> tuple[AgentDraftProposal, bool]:
        """Insert once; a replay returns the stored proposal for the caller to compare."""
        collection = await self._collection()
        try:
            await collection.insert_one(to_document(record))
            return record, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise ProposalUnavailable("Proposal write outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.proposal_id)
        if stored is None:
            raise ProposalUnavailable("Proposal write outcome is unknown")
        return stored, False

    async def list_for_draft(
        self,
        tenant_id: str,
        owner_subject_id: str,
        draft_id: str,
        *,
        statuses: tuple[str, ...] | None,
        limit: int,
        after: tuple[str, str] | None,
    ) -> tuple[list[AgentDraftProposal], tuple[str, str] | None]:
        """One page of a draft's proposals for its owner, newest first."""
        collection = await self._collection()
        if not self._indexed:
            try:
                await collection.create_index(
                    [
                        ("tenant_id", 1),
                        ("draft_id", 1),
                        ("owner_subject_id", 1),
                        ("sort_key", DESCENDING),
                        ("_id", DESCENDING),
                    ],
                    name=LISTING_INDEX,
                )
            except PyMongoError as exc:
                raise ProposalUnavailable("Proposal listing index is unavailable") from exc
            self._indexed = True
        query: dict[str, Any] = {
            "tenant_id": tenant_id,
            "draft_id": draft_id,
            "owner_subject_id": owner_subject_id,
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
                await collection.find(query)
                .sort([("sort_key", DESCENDING), ("_id", DESCENDING)])
                .limit(limit + 1)
                .to_list(limit + 1)
            )
        except PyMongoError as exc:
            raise ProposalUnavailable("Proposal store is unavailable") from exc
        page = documents[:limit]
        following = (page[-1]["sort_key"], page[-1]["_id"]) if len(documents) > limit else None
        return [from_document(document) for document in page], following

    async def get(self, tenant_id: str, proposal_id: str) -> AgentDraftProposal | None:
        collection = await self._collection()
        try:
            document = await collection.find_one({"_id": proposal_id, "tenant_id": tenant_id})
        except PyMongoError as exc:
            raise ProposalUnavailable("Proposal store is unavailable") from exc
        return None if document is None else from_document(document)

    async def transition(
        self,
        record: AgentDraftProposal,
        *,
        expected: ProposalStatus,
        claim_digest: str | None,
        changes: dict[str, Any],
        claim_attempt: int | None = None,
    ) -> AgentDraftProposal:
        """Compare-and-set from ``expected`` holding exactly ``claim_digest`` (``None``:
        no claim). Returns the stored proposal; the caller inspects who won."""
        if not changes or not set(changes) <= _MUTABLE:
            raise ValueError("Only proposal decision fields can change")
        updated = record.model_copy(update=changes)
        # Revalidate the full record so an invalid state is never written.
        AgentDraftProposal.model_validate(updated.model_dump())
        document = to_document(updated)
        values = {key: document.get(key) for key in changes}
        collection = await self._collection()
        try:
            await collection.update_one(
                {
                    "_id": record.proposal_id,
                    "tenant_id": record.tenant_id,
                    "proposal_digest": record.proposal_digest,
                    "status": expected,
                    "claim.decision_digest": claim_digest,
                    **({} if claim_attempt is None else {"claim.attempt": claim_attempt}),
                },
                {"$set": values},
            )
        except PyMongoError as exc:
            raise ProposalUnavailable("Proposal transition outcome is unknown") from exc
        stored = await self.get(record.tenant_id, record.proposal_id)
        if stored is None:
            raise CorruptProposal("Proposal disappeared during a transition")
        return stored
