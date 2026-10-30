"""Durable Manuscript sessions (``caos.manuscript_session`` v1) and typed failures.

A session pins one exact Manuscript (``id@version`` and fingerprint) for one requester,
tenant and source Agent. It records the current stage, completed stages and validated
answers with provenance; never conversations, prompts, permits or credentials.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.base.operations.v2.admission import Digest, sha256_hex

SESSIONS_COLLECTION = "coll_manuscript_sessions"
MAX_APPLIED = 32
SessionStatus = Literal["active", "completed", "restarted"]
AnswerSource = Literal["human", "agent", "prefill"]


class ManuscriptError(Exception):
    """Typed failure; messages never contain answer values."""

    code = "manuscript.error"
    retryable = False


class SessionNotFound(ManuscriptError):
    """Missing, or another owner's, tenant's or source Agent's: indistinguishable."""

    code = "session.not_found"


class SessionRevisionConflict(ManuscriptError):
    code = "session.revision_conflict"

    def __init__(self, current_revision: int) -> None:
        super().__init__("The session changed since the expected revision")
        self.current_revision = current_revision


class SessionClosed(ManuscriptError):
    def __init__(self, status: str) -> None:
        super().__init__("The session no longer accepts changes")
        self.code = f"session.{status}"


class SessionIncomplete(ManuscriptError):
    code = "session.incomplete"

    def __init__(self, missing: list[str]) -> None:
        super().__init__("The interview is not complete")
        self.missing = missing


class SessionInvalidRequest(ManuscriptError):
    code = "session.invalid_request"


class SessionIdempotencyConflict(ManuscriptError):
    code = "idempotency.conflict"


class ManuscriptNotAvailable(ManuscriptError):
    """The pinned Manuscript (exact version and fingerprint) is not registered."""

    code = "manuscript.unavailable"


class SessionCorrupt(ManuscriptError):
    code = "session.corrupt"


class SessionUnavailable(ManuscriptError):
    code = "session.unavailable"
    retryable = True


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PinnedManuscript(_Frozen):
    id: str
    version: str
    fingerprint: Digest

    def key(self) -> str:
        return f"{self.id}@{self.version}"


class Answer(_Frozen):
    value: Any
    source: AnswerSource
    revision: StrictInt = Field(ge=1)


class AppliedRequest(_Frozen):
    """A recently applied request, so a retry replays instead of applying twice."""

    key: str
    digest: Digest
    revision: StrictInt = Field(ge=1)


class ManuscriptSession(_Frozen):
    contract: Literal["caos.manuscript_session"] = "caos.manuscript_session"
    schema_version: Literal[1] = 1
    session_id: str
    tenant_id: str
    owner_subject_id: str
    source_agent_id: str
    manuscript: PinnedManuscript
    draft_id: str | None
    start_digest: Digest
    revision: StrictInt = Field(ge=1)
    status: SessionStatus
    stage: str
    completed_stages: list[str]
    answers: dict[str, Answer]
    artifact: dict[str, Any] | None = None
    artifact_digest: Digest | None = None
    restarted_from: str | None = None
    restarted_to: str | None = None
    applied: list[AppliedRequest] = Field(default_factory=list, max_length=MAX_APPLIED)
    created_at: datetime
    updated_at: datetime
    state_digest: Digest

    @classmethod
    def build(cls, **fields: Any) -> ManuscriptSession:
        unsigned = cls.model_construct(**fields, state_digest="0" * 64)
        return cls.model_validate({**fields, "state_digest": unsigned.digest()})

    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude={"state_digest"}))

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.state_digest != self.digest():
            raise ValueError("Session state digest mismatch")
        # A restarted session keeps its artifact (if it had one) read-only.
        if (self.status == "completed" and self.artifact is None) or (
            self.status == "active" and self.artifact is not None
        ):
            raise ValueError("Only a completed (or restarted) session holds an artifact")
        if (self.artifact is None) != (self.artifact_digest is None) or (
            self.artifact is not None and self.artifact_digest != sha256_hex(self.artifact)
        ):
            raise ValueError("Artifact digest mismatch")
        if (self.status == "restarted") != (self.restarted_to is not None):
            raise ValueError("Only a restarted session names its successor")
        if len(set(self.completed_stages)) != len(self.completed_stages):
            raise ValueError("A stage is completed at most once")
        if any(answer.revision > self.revision for answer in self.answers.values()):
            raise ValueError("An answer cannot name a future revision")
        if self.updated_at < self.created_at:
            raise ValueError("A session cannot be updated before it was created")
        return self

    def values(self) -> dict[str, Any]:
        return {key: answer.value for key, answer in self.answers.items()}

    def replay(self, key: str, digest: str) -> bool:
        """True if this exact request was applied; a reused key for another request is
        an idempotency conflict."""
        for applied in self.applied:
            if applied.key == key:
                if applied.digest != digest:
                    raise SessionIdempotencyConflict("Request key reused for another request")
                return True
        return False
