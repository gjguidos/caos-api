"""``caos.activity_context`` v1: governed links between an activity and independently
owned resources (AGENT-CTX-0.1, ADR-0004).

A context belongs to one *scope*: ``(tenant, owner resource, human, principal)``. Within
a scope, interview-like activities run as immutable *generations*. The per-scope head
document is the only authority for which generation is active, so at most one is active
by construction (``_id`` uniqueness and single-document compare-and-set only).

Records store typed references, observed revisions, bounded causation and pending
evidence obligations: never permits, credentials, messages, model output, answers or
resource content. A context grants nothing; every reference is re-resolved through its
owning service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from src.base.operations.v2.admission import Digest, sha256_hex
from src.domains.agentverse.audit.models import ActivityContextMetadata

CONTEXT_CONTRACT: Final = "caos.activity_context"
HEAD_CONTRACT: Final = "caos.activity_scope_head"
CONTEXTS_COLLECTION: Final = "coll_activity_contexts"
HEADS_COLLECTION: Final = "coll_activity_context_heads"
MAX_GENERATIONS: Final = 64
MAX_APPLIED: Final = 32
MAX_SLOTS: Final = 8

ContextStatus = Literal["active", "closed"]
ClosedReason = Literal["completed", "switched", "abandoned", "reset"]
EvidenceType = Literal[
    "activity.context.started", "activity.context.switched", "activity.context.closed"
]
Ref = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w.@:-]+$")]
Kind = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=64)
]
SlotName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
OperationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
Revision = Annotated[StrictInt, Field(ge=1)]


# --- Errors ---------------------------------------------------------------------------------


class ActivityError(Exception):
    """Typed failure; messages never name another owner's resources."""

    code = "activity.error"
    retryable = False


class ActivityNotFound(ActivityError):
    """Missing, or another tenant's, human's, owner's or principal's: indistinguishable."""

    code = "activity.not_found"


class ActivityUnavailable(ActivityError):
    code = "activity.unavailable"
    retryable = True


class ActivityCorrupt(ActivityError):
    code = "activity.corrupt"


class ActivityContractUnavailable(ActivityError):
    """Unknown activity contract, version or fingerprint, or not allowed for the owner."""

    code = "activity.contract_unavailable"


class ActivityActiveExists(ActivityError):
    code = "activity.active_exists"


class ActivityClosed(ActivityError):
    code = "activity.closed"


class ActivityRevisionConflict(ActivityError):
    code = "activity.revision_conflict"

    def __init__(self, current_revision: int) -> None:
        super().__init__("The context changed since the expected revision")
        self.current_revision = current_revision


class ActivityOperationConflict(ActivityError):
    """The operation ID was already used for a different transition."""

    code = "activity.operation_conflict"


class ActivityStaleOwner(ActivityError):
    """The calling owner attempt (e.g. a conversation turn) lost ownership or is older
    than the last writer."""

    code = "activity.stale_owner"


class ActivityInvalidReferences(ActivityError):
    """References do not match the contract's typed slots."""

    code = "activity.invalid_references"


class ActivityReferenceUnavailable(ActivityError):
    """A referenced resource is unavailable, not the actor's, in a disallowed state, or
    inconsistent with another slot."""

    def __init__(self, slot: str, reason: str = "unavailable") -> None:
        super().__init__("A referenced resource is unavailable")
        self.code = f"activity.reference_{reason}"
        self.slot = slot


class ActivityLimit(ActivityError):
    code = "activity.generation_limit"


# --- Value objects ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ActorScope(_Frozen):
    """The verified human and the Agent acting for them (System Citizens: not yet)."""

    subject_id: Ref
    principal_id: Ref
    principal_kind: Literal["agent"]


class ActivityRef(_Frozen):
    key: Ref  # exact id@version
    fingerprint: Digest


class OwnerRef(_Frozen):
    kind: Kind
    id: Ref


class ResourceRef(_Frozen):
    kind: Kind
    id: Ref
    observed_revision: Revision


class Causation(_Frozen):
    """Trusted owner-attempt correlation (a conversation turn's event and fence)."""

    event_id: Ref
    fence: Revision
    operation_id: OperationId


class AppliedOperation(_Frozen):
    operation_id: OperationId
    digest: Digest
    revision: Revision


class EvidenceObligation(_Frozen):
    """A consequential transition's Seshat record, committed with the transition and
    delivered later (outbox). ``event_id`` is deterministic."""

    event_id: Ref
    event_type: EvidenceType
    causation_id: Ref
    metadata: ActivityContextMetadata


References = Annotated[dict[SlotName, ResourceRef], Field(max_length=MAX_SLOTS)]


class ActivityContext(_Frozen):
    contract: Literal["caos.activity_context"] = CONTEXT_CONTRACT
    schema_version: Literal[1] = 1
    context_id: Ref
    tenant_id: Ref
    scope_key: Digest
    actor: ActorScope
    owner: OwnerRef
    activity: ActivityRef
    generation: Revision
    status: ContextStatus
    closed_reason: ClosedReason | None = None
    previous_context_id: Ref | None = None
    references: References
    revision: Revision
    # Immutable creation facts (evidence is rebuilt only from these, never from later state).
    origin: Literal["start", "switch"]
    start_digest: Digest
    started_references: References
    created_by: Causation
    last_owner: Causation
    applied: Annotated[list[AppliedOperation], Field(max_length=MAX_APPLIED)]
    pending_evidence: Annotated[list[EvidenceObligation], Field(max_length=4)] = Field(
        default_factory=list
    )
    created_at: datetime
    updated_at: datetime
    state_digest: Digest

    @classmethod
    def build(cls, **fields: Any) -> ActivityContext:
        unsigned = cls.model_construct(**fields, state_digest="0" * 64)
        return cls.model_validate({**fields, "state_digest": unsigned.digest()})

    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude={"state_digest"}))

    def replay(self, operation_id: str, digest: str) -> bool:
        """``True`` if this exact operation was applied; a reused ID is a conflict."""
        for applied in self.applied:
            if applied.operation_id == operation_id:
                if applied.digest != digest:
                    raise ActivityOperationConflict("Operation ID reused for another transition")
                return True
        return False

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.state_digest != self.digest():
            raise ValueError("Context state digest mismatch")
        if (self.status == "closed") != (self.closed_reason is not None):
            raise ValueError("Only a closed context names its closure reason")
        if (self.generation == 1) != (self.previous_context_id is None):
            raise ValueError("Exactly every later generation names its predecessor")
        if self.updated_at < self.created_at or self.last_owner.fence < self.created_by.fence:
            raise ValueError("Context history cannot move backward")
        return self


class ActivationIntent(_Frozen):
    """Durable successor intent: the exact generation to activate (and the predecessor
    to close), recorded in the same head write that makes it active."""

    operation_id: OperationId
    digest: Digest
    successor: ActivityContext
    close_previous: Ref | None = None


class ActivityScopeHead(_Frozen):
    """The only authority for which generation of a scope is active."""

    contract: Literal["caos.activity_scope_head"] = HEAD_CONTRACT
    schema_version: Literal[1] = 1
    scope_key: Digest
    tenant_id: Ref
    actor: ActorScope
    owner: OwnerRef
    generation: StrictInt = Field(ge=0, le=MAX_GENERATIONS)
    active_context_id: Ref | None = None
    intent: ActivationIntent | None = None
    revision: StrictInt = Field(ge=0)
    updated_at: datetime
    state_digest: Digest

    @classmethod
    def build(cls, **fields: Any) -> ActivityScopeHead:
        unsigned = cls.model_construct(**fields, state_digest="0" * 64)
        return cls.model_validate({**fields, "state_digest": unsigned.digest()})

    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude={"state_digest"}))

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.state_digest != self.digest():
            raise ValueError("Head state digest mismatch")
        if self.intent is not None and (
            self.intent.successor.context_id != self.active_context_id
            or self.intent.successor.generation != self.generation
        ):
            raise ValueError("A pending activation is the active generation")
        return self
