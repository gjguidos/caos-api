"""Durable Agent creation draft (DRAFT-0.1).

A draft is the human's work in progress toward one ``AgentCreationInput``. Every
committed revision is an immutable, digest-chained snapshot; the current draft is the
latest committed revision. A draft is never a review, an approval or any authority:
validation, approval and creation stay with G3.3/G3.4.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self, get_args
from uuid import NAMESPACE_URL, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from src.base.operations.v2.admission import Digest, canonical_json, sha256_hex
from src.domains.agentverse.entities.gui_components import GuiComponent
from src.domains.agentverse.tools.validation_tool.models import ToolRefInput

DRAFT_CONTRACT = "caos.agent_draft"
DRAFT_UPDATE_CONTRACT = "caos.agent_draft_update"
REVISION_CONTRACT = "caos.agent_draft_revision"
# The existing G3.2/G3.3 draft envelope; a draft's content is posted to review as-is.
CREATION_DRAFT_CONTRACT = "caos.agent_creation_draft"
DRAFTS_COLLECTION = "coll_agent_drafts"
REVISIONS_COLLECTION = "coll_agent_draft_revisions"
# Canonical content bound; Agent definitions are small and a draft is not a file store.
MAX_CONTENT_BYTES = 128 * 1024

# ``editing``: humans may change it. ``approved``: frozen after an effective G3.3
# approval of an exact revision (reopening needs a new review). ``consumed``: G3.4
# confirmed the creation; read-only forever (fork to continue).
DraftStatus = Literal["editing", "approved", "consumed"]
# ``user``: a human write through the draft API. ``proposal``: a human-accepted field
# of a proposal (DRAFT-0.2). A client never chooses its own provenance.
ProvenanceOrigin = Literal["user", "proposal"]
DraftField = Literal[
    "name",
    "type",
    "role_name",
    "personality_key",
    "prompt",
    "visibility",
    "whitelisted_groups",
    "tools",
    "workflows",
    "rule_pack_ids",
    "gui_components",
]

Key = Annotated[str, StringConstraints(max_length=200)]


class DraftError(Exception):
    """Typed draft failure; messages never contain draft content."""

    code = "draft.error"
    retryable = False


class DraftNotFound(DraftError):
    """Missing, or owned by another requester, tenant or source: indistinguishable."""

    code = "draft.not_found"


class RevisionConflict(DraftError):
    code = "draft.revision_conflict"

    def __init__(self, current_revision: int) -> None:
        super().__init__("The draft changed since the expected revision")
        self.current_revision = current_revision


class DraftIdempotencyConflict(DraftError):
    code = "idempotency.conflict"


class DraftUnavailable(DraftError):
    """Persistence outcome unknown: read the draft, then repeat if appropriate."""

    code = "draft.unavailable"
    retryable = True


class CorruptDraft(DraftError):
    code = "draft.corrupt"


class DraftLocked(DraftError):
    """The draft is not editable in its current lifecycle state."""

    def __init__(self, status: str, current_revision: int) -> None:
        super().__init__("The draft is not editable in its current state")
        self.code = f"draft.{status}"
        self.current_revision = current_revision


class DraftCreationInProgress(DraftError):
    """A creation already consumed this draft's approval and is not finished."""

    code = "draft.creation_in_progress"


class DraftInvalidRequest(DraftError):
    code = "draft.invalid_request"


class AgentDraftContent(BaseModel):
    """Partial ``AgentCreationInput``: the same fields, each optional while editing.

    Structure is enforced here (types, closed field set, server-owned fields rejected,
    bounded size); completeness, naming rules and references are G3.3 validation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: Annotated[str, StringConstraints(max_length=100)] | None = None
    type: Key | None = None
    role_name: Key | None = None
    personality_key: Key | None = None
    prompt: str | None = None
    visibility: Literal["public", "private"] | None = None
    whitelisted_groups: Annotated[list[Key], Field(max_length=100)] | None = None
    tools: Annotated[list[ToolRefInput], Field(max_length=100)] | None = None
    workflows: Annotated[list[Key], Field(max_length=100)] | None = None
    rule_pack_ids: Annotated[list[Key], Field(max_length=100)] | None = None
    gui_components: Annotated[list[GuiComponent], Field(max_length=20)] | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if len(canonical_json(self.data()).encode()) > MAX_CONTENT_BYTES:
            raise ValueError("Draft content is too large")
        return self

    def data(self) -> dict[str, Any]:
        """Canonical content: present fields only, JSON types."""
        return self.model_dump(mode="json", exclude_none=True)


class AgentDraftCreateRequest(BaseModel):
    """The existing draft envelope with typed, partial content."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_creation_draft"]
    schema_version: StrictInt = Field(ge=1, le=1)
    resource_type: Literal["agent"]
    creation: AgentDraftContent = Field(default_factory=AgentDraftContent)

    def request_digest(self, source_agent_id: str) -> str:
        return sha256_hex({"source_agent_id": source_agent_id, "content": self.creation.data()})


class AgentDraftForkRequest(BaseModel):
    """Start a new draft from one of the requester's own committed revisions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_fork"]
    schema_version: StrictInt = Field(ge=1, le=1)
    revision: StrictInt = Field(ge=1)


Listing = Literal["editing", "approved", "consumed", "open", "all"]
LISTING_STATUSES: dict[str, tuple[str, ...] | None] = {
    "editing": ("editing",),
    "approved": ("approved",),
    "consumed": ("consumed",),
    # Not consumed: what a form may resume.
    "open": ("editing", "approved"),
    "all": None,
}
MAX_PAGE = 50


def encode_cursor(position: tuple[str, str], scope: dict[str, Any]) -> str:
    """Opaque continuation bound to its query; grants nothing (filters are re-applied)."""
    payload = {"k": position[0], "i": position[1], "f": sha256_hex(scope)[:32]}
    return base64.urlsafe_b64encode(canonical_json(payload).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, scope: dict[str, Any]) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if (
            not isinstance(payload, dict)
            or payload.get("f") != sha256_hex(scope)[:32]
            or not isinstance(payload.get("k"), str)
            or not isinstance(payload.get("i"), str)
            or len(cursor) > 512
        ):
            raise ValueError("Cursor does not continue this query")
        return payload["k"], payload["i"]
    except (ValueError, TypeError, binascii.Error) as exc:
        raise DraftInvalidRequest("Invalid cursor") from exc


class AgentDraftUpdateRequest(BaseModel):
    """Compare-and-set update against the revision the human edited."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_update"]
    schema_version: StrictInt = Field(ge=1, le=1)
    expected_revision: StrictInt = Field(ge=1)
    changes: AgentDraftContent = Field(default_factory=AgentDraftContent)
    remove: list[DraftField] = Field(default_factory=list, max_length=len(get_args(DraftField)))

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        if len(set(self.remove)) != len(self.remove):
            raise ValueError("Duplicate removed field")
        if set(self.remove) & set(self.changes.data()):
            raise ValueError("A field cannot be both changed and removed")
        return self


def _absent(value: Any) -> bool:
    return value is None


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: ProvenanceOrigin
    revision: StrictInt = Field(ge=1)
    # Omitted when absent, so DRAFT-0.1 revision digests are unchanged.
    proposal_id: str | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def _proposal_origin(self) -> Self:
        if (self.origin == "proposal") != (self.proposal_id is not None):
            raise ValueError("Only proposal provenance names a proposal")
        return self


_CAUSE_FIELDS: dict[str, frozenset[str]] = {
    "proposal": frozenset({"proposal_id", "decision_digest", "accepted_fields"}),
    "approval": frozenset({"approval_id", "review_digest", "reviewed_revision"}),
    "reopen": frozenset({"approval_id"}),
    "consumption": frozenset(
        {"approval_id", "review_digest", "reviewed_revision", "creation_id", "agent_id"}
    ),
}


class RevisionCause(BaseModel):
    """Why a revision exists when it is not a direct human edit.

    ``proposal``: an accepted proposal and the exact decision (recovery and replay find an
    acceptance by it). Lifecycle causes never change content: ``approval`` (an effective
    G3.3 approval of ``reviewed_revision``), ``reopen`` (that approval withdrawn or spent
    by a failed creation) and ``consumption`` (G3.4 confirmed ``agent_id``). Absent fields
    are omitted, so earlier revision digests are unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["proposal", "approval", "reopen", "consumption"]
    proposal_id: str | None = Field(default=None, exclude_if=_absent)
    decision_digest: Digest | None = Field(default=None, exclude_if=_absent)
    accepted_fields: list[DraftField] | None = Field(default=None, exclude_if=_absent)
    approval_id: str | None = Field(default=None, exclude_if=_absent)
    review_digest: Digest | None = Field(default=None, exclude_if=_absent)
    reviewed_revision: StrictInt | None = Field(default=None, ge=1, exclude_if=_absent)
    creation_id: str | None = Field(default=None, exclude_if=_absent)
    agent_id: str | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        present = {
            name
            for name in type(self).model_fields
            if name != "kind" and getattr(self, name) is not None
        }
        if present != _CAUSE_FIELDS[self.kind]:
            raise ValueError("Revision cause fields do not match its kind")
        if self.accepted_fields is not None and not self.accepted_fields:
            raise ValueError("An acceptance names at least one field")
        return self


class ForkOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    revision: StrictInt = Field(ge=1)


def draft_id(tenant_id: str, subject_id: str, source_agent_id: str, idempotency_key: str) -> str:
    """Stable identity: one requester's key for one source always names the same draft."""
    return str(
        uuid5(
            NAMESPACE_URL,
            "caos:agent-draft:"
            + sha256_hex(
                {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "source_agent_id": source_agent_id,
                    "key": idempotency_key,
                }
            ),
        )
    )


def pointer(field: str) -> str:
    return "/" + field


class AgentDraftRevision(BaseModel):
    """One committed, immutable revision. Ownership comes only from the verified
    session; ``revision_digest`` covers every other field and chains to the parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_draft_revision"] = "caos.agent_draft_revision"
    schema_version: Literal[1] = 1
    draft_id: str
    revision: StrictInt = Field(ge=1)
    tenant_id: str
    owner_subject_id: str
    source_agent_id: str
    status: DraftStatus
    content: dict[str, Any]
    provenance: dict[str, FieldProvenance]
    content_digest: Digest
    creation_request_digest: Digest
    parent_digest: Digest | None
    created_at: datetime
    committed_at: datetime
    # Omitted when absent, so DRAFT-0.1 revision digests are unchanged.
    cause: RevisionCause | None = Field(default=None, exclude_if=_absent)
    # Draft-level, like ``created_at``: carried by every revision of a fork.
    forked_from: ForkOrigin | None = Field(default=None, exclude_if=_absent)
    revision_digest: Digest

    @classmethod
    def build(cls, **fields: Any) -> AgentDraftRevision:
        content = AgentDraftContent.model_validate(fields["content"]).data()
        fields = {**fields, "content": content, "content_digest": sha256_hex(content)}
        unsigned = cls.model_construct(**fields, revision_digest="0" * 64)
        return cls.model_validate({**fields, "revision_digest": unsigned.digest()})

    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude={"revision_digest"}))

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.content_digest != sha256_hex(self.content):
            raise ValueError("Draft content digest mismatch")
        if self.revision_digest != self.digest():
            raise ValueError("Draft revision digest mismatch")
        if (self.revision == 1) != (self.parent_digest is None):
            raise ValueError("Only the first revision has no parent")
        if set(self.provenance) != {pointer(field) for field in self.content}:
            raise ValueError("Every present field has exactly one provenance")
        if any(item.revision > self.revision for item in self.provenance.values()):
            raise ValueError("Provenance cannot name a future revision")
        if self.committed_at < self.created_at:
            raise ValueError("A revision cannot precede its draft")
        lifecycle = {"approved": "approval", "consumed": "consumption"}.get(self.status)
        if lifecycle is not None and (self.cause is None or self.cause.kind != lifecycle):
            raise ValueError("A frozen or consumed revision names its lifecycle cause")
        if (
            self.status == "editing"
            and self.cause is not None
            and self.cause.kind
            in (
                "approval",
                "consumption",
            )
        ):
            raise ValueError("An editing revision cannot carry a frozen lifecycle cause")
        proposed = {
            item.proposal_id
            for item in self.provenance.values()
            if item.revision == self.revision and item.origin == "proposal"
        }
        if proposed and (self.cause is None or proposed != {self.cause.proposal_id}):
            raise ValueError("Proposal provenance requires its proposal cause")
        return self

    def creation_draft(self) -> dict[str, Any]:
        """The G3.3 review envelope for exactly this revision's content."""
        return {
            "contract": CREATION_DRAFT_CONTRACT,
            "schema_version": 1,
            "resource_type": "agent",
            "creation": dict(self.content),
        }

    def lifecycle(self) -> dict[str, Any] | None:
        """The approval (and confirmed creation) that froze this draft, if any."""
        cause = self.cause
        if self.status == "editing" or cause is None:
            return None
        return {
            "approval_id": cause.approval_id,
            "review_digest": cause.review_digest,
            "reviewed_revision": cause.reviewed_revision,
            "creation_id": cause.creation_id,
            "agent_id": cause.agent_id,
        }

    def public(self) -> dict[str, Any]:
        """Requester projection: no storage identity, tenant or internal digests."""
        return {
            "contract": DRAFT_CONTRACT,
            "schema_version": 1,
            "draft_id": self.draft_id,
            "source_agent_id": self.source_agent_id,
            "revision": self.revision,
            "status": self.status,
            "draft": self.creation_draft(),
            "provenance": {
                key: item.model_dump(mode="json") for key, item in sorted(self.provenance.items())
            },
            "content_digest": self.content_digest,
            "lifecycle": self.lifecycle(),
            "forked_from": None
            if self.forked_from is None
            else self.forked_from.model_dump(mode="json"),
            "created_at": _timestamp(self.created_at),
            "updated_at": _timestamp(self.committed_at),
            # A draft is never a review, approval or creation/activation authority.
            "authorizes": {
                "review": False,
                "approval": False,
                "creation": False,
                "activation": False,
            },
        }


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
