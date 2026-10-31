"""Human-owned Agent revision drafts (REV-2, ADR-0005).

A revision draft holds one **complete** candidate definition (``AgentCreationInput``:
every revisable field, never a patch) for one governed Agent, against the exact
predecessor it was started from. Each human edit is an immutable draft revision; inserting
revision N+1 (``_id`` uniqueness) is the compare-and-set, so concurrent edits from the same
revision have exactly one winner. A draft changes no Agent definition, attaches no Tool,
grants nothing and is never a revision record.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import MongoDBRepository

DRAFT_CONTRACT: Final = "caos.agent_definition_revision_draft"
DRAFTS_COLLECTION: Final = "coll_agent_definition_revision_drafts"
MAX_DRAFT_REVISIONS: Final = 200
# Fields of a governed definition a revision may change; everything else (identity,
# tenant, ownership, exposure, runtime, backends, DNA, routes, collections) is carried
# unchanged from the predecessor.
REVISABLE_FIELDS: Final = tuple(AgentCreationInput.model_fields)

IdempotencyKey = Annotated[
    str, StringConstraints(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class RevisionDraftError(Exception):
    """Typed failure; messages never contain draft content."""

    code = "revision_draft.error"
    retryable = False


class RevisionDraftNotFound(RevisionDraftError):
    """Missing, or another tenant's, owner's or Agent's: indistinguishable."""

    code = "revision_draft.not_found"


class RevisionDraftConflict(RevisionDraftError):
    code = "revision_draft.revision_conflict"

    def __init__(self, current_revision: int) -> None:
        super().__init__("The draft changed since the expected revision")
        self.current_revision = current_revision


class RevisionDraftIdempotencyConflict(RevisionDraftError):
    code = "idempotency.conflict"


class RevisionDraftUnavailable(RevisionDraftError):
    code = "revision_draft.unavailable"
    retryable = True


class RevisionDraftCorrupt(RevisionDraftError):
    code = "revision_draft.corrupt"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PredecessorRef(_Frozen):
    """The exact definition a candidate revises.

    ``materialized`` is false for a verified creation baseline that has no stored
    revision-1 record yet: the digests are those revision 1 *would* have. Nothing creates
    that record during authoring or review.
    """

    revision: StrictInt = Field(ge=1)
    revision_digest: Digest
    definition_digest: Digest
    materialized: bool


def revision_draft_id(tenant_id: str, subject_id: str, agent_id: str, key: str) -> str:
    """Stable identity: one requester's key for one Agent always names the same draft."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-definition-revision-draft:"
            + sha256_hex(
                {"tenant": tenant_id, "subject": subject_id, "agent": agent_id, "key": key}
            ),
        )
    )


def candidate_digest(candidate: dict[str, Any]) -> str:
    return sha256_hex({"contract": DRAFT_CONTRACT, "candidate": candidate})


class RevisionDraft(_Frozen):
    """One immutable revision of a revision draft."""

    contract: Literal["caos.agent_definition_revision_draft"] = DRAFT_CONTRACT
    schema_version: Literal[1] = 1
    draft_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    revision: StrictInt = Field(ge=1, le=MAX_DRAFT_REVISIONS)
    tenant_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    owner_subject_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    predecessor: PredecessorRef
    candidate: dict[str, Any]
    candidate_digest: Digest
    # Idempotency of the creating request (same key, same request).
    request_digest: Digest
    parent_digest: Digest | None
    authorization: AuthorizationProvenance
    created_at: datetime
    committed_at: datetime
    state_digest: Digest

    @classmethod
    def build(cls, **fields: Any) -> RevisionDraft:
        candidate = AgentCreationInput.model_validate(fields["candidate"]).model_dump(mode="json")
        fields = {**fields, "candidate": candidate, "candidate_digest": candidate_digest(candidate)}
        draft = cls.model_construct(**fields, state_digest="0" * 64)
        return cls.model_validate({**fields, "state_digest": draft.digest()})

    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude={"state_digest"}))

    def public(self) -> dict[str, Any]:
        """Requester projection: the owner's own candidate; no provenance or digests of
        internal authority."""
        return {
            "contract": self.contract,
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "revision": self.revision,
            "agent_id": self.agent_id,
            "predecessor": {
                "revision": self.predecessor.revision,
                "revision_digest": self.predecessor.revision_digest,
                "materialized": self.predecessor.materialized,
            },
            "candidate": self.candidate,
            "candidate_digest": self.candidate_digest,
            "committed_at": self.committed_at.isoformat(),
            "authorizes": {"revision": False, "approval": False, "application": False},
        }

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.state_digest != self.digest():
            raise ValueError("Draft state digest mismatch")
        if self.candidate_digest != candidate_digest(self.candidate):
            raise ValueError("Candidate digest mismatch")
        AgentCreationInput.model_validate(self.candidate)  # complete and strict
        if (self.revision == 1) != (self.parent_digest is None):
            raise ValueError("Exactly the first draft revision has no parent")
        if (self.authorization.tenant_id, self.authorization.subject_id) != (
            self.tenant_id,
            self.owner_subject_id,
        ):
            raise ValueError("Draft provenance differs from its owner")
        if self.committed_at < self.created_at:
            raise ValueError("Draft history cannot move backward")
        return self


class RevisionDraftCreateRequest(_Frozen):
    """Start a draft from a complete candidate, or (``None``) from the current definition."""

    contract: Literal["caos.agent_definition_revision_draft_create"]
    schema_version: StrictInt = Field(ge=1, le=1)
    candidate: AgentCreationInput | None = None

    def request_digest(self, agent_id: str) -> str:
        return sha256_hex(
            {
                "agent_id": agent_id,
                "candidate": None
                if self.candidate is None
                else self.candidate.model_dump(mode="json"),
            }
        )


class RevisionDraftUpdateRequest(_Frozen):
    """Replace the candidate (complete, never a patch) against the edited revision."""

    contract: Literal["caos.agent_definition_revision_draft_update"]
    schema_version: StrictInt = Field(ge=1, le=1)
    expected_revision: StrictInt = Field(ge=1)
    candidate: AgentCreationInput


def _draft_doc_id(tenant_id: str, draft_id: str, revision: int) -> str:
    return sha256_hex(
        {"contract": DRAFT_CONTRACT, "tenant": tenant_id, "draft": draft_id, "rev": revision}
    )


class MongoRevisionDraftRepository:
    """Insert-only draft revisions keyed by ``_id`` (MongoDB / Cosmos Mongo API parity)."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection: str = DRAFTS_COLLECTION
    ) -> None:
        self.repository = mongodb_repository
        self.collection = collection

    async def _collection(self) -> Any:
        try:
            collection = await self.repository.get_collection(self.collection)
        except PyMongoError as exc:
            raise RevisionDraftUnavailable("Revision draft store is unavailable") from exc
        # Motor's stub types read_preference as ReadPreference; Primary is its runtime value.
        return collection.with_options(read_preference=ReadPreference.PRIMARY)  # type: ignore[arg-type]

    async def revision(self, tenant_id: str, draft_id: str, revision: int) -> RevisionDraft | None:
        collection = await self._collection()
        try:
            document = await collection.find_one(
                {"_id": _draft_doc_id(tenant_id, draft_id, revision)}
            )
        except PyMongoError as exc:
            raise RevisionDraftUnavailable("Revision draft store is unavailable") from exc
        if document is None:
            return None
        try:
            data = dict(document)
            data.pop("_id")
            record = RevisionDraft.model_validate_json(json.dumps(data))
        except (ValueError, TypeError) as exc:
            raise RevisionDraftCorrupt("Invalid stored revision draft") from exc
        if (record.tenant_id, record.draft_id, record.revision) != (tenant_id, draft_id, revision):
            raise RevisionDraftCorrupt("Stored draft identity mismatch")
        return record

    async def current(self, tenant_id: str, draft_id: str) -> RevisionDraft | None:
        """The latest committed revision (exact ``_id`` lookups only; bounded)."""
        latest: RevisionDraft | None = None
        for revision in range(1, MAX_DRAFT_REVISIONS + 1):
            record = await self.revision(tenant_id, draft_id, revision)
            if record is None:
                return latest
            if latest is not None and record.parent_digest != latest.state_digest:
                raise RevisionDraftCorrupt("Draft revision chain is broken")
            latest = record
        return latest

    async def commit(self, draft: RevisionDraft) -> tuple[RevisionDraft, bool]:
        """Insert revision N; ``(stored, False)`` if that exact revision already exists.

        The caller compares the stored revision with its intent: a different winner is a
        conflict.
        """
        collection = await self._collection()
        document = {
            "_id": _draft_doc_id(draft.tenant_id, draft.draft_id, draft.revision),
            **json.loads(draft.model_dump_json()),
        }
        try:
            await collection.insert_one(document)
            return draft, True
        except DuplicateKeyError:
            pass
        except PyMongoError as exc:
            raise RevisionDraftUnavailable("Draft write outcome is unknown") from exc
        stored = await self.revision(draft.tenant_id, draft.draft_id, draft.revision)
        if stored is None:
            raise RevisionDraftUnavailable("Draft write outcome is unknown")
        return stored, False
