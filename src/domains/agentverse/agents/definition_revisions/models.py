"""``caos.agent_definition_revision`` v1: one immutable Agent definition revision (REV-1).

A revision record is a **selected successor**: the decided definition N of one Agent.
It is not an approved candidate (a human decision, REV-3), not proof that the Agent
projection in ``coll_agents`` was updated (application, REV-4), and not a reconciled
runtime (REV-5). Records are insert-only and identified by ``(tenant, Agent, revision)``.

Revision 1 is the creation **baseline**: the definition as created, admitted only with
verified historical creation evidence. Later revisions chain to their exact predecessor
by revision digest. Records hold definitions, digests and bounded correlation only:
never permits, credentials, conversation content or model output.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from src.base.operations.v2.admission import AuthorizationProvenance, Digest, sha256_hex
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CANONICAL_SCHEMA,
    CANONICAL_V1_FIELDS,
    definition_digest,
)

REVISION_CONTRACT: Final = "caos.agent_definition_revision"
REVISIONS_COLLECTION: Final = "coll_agent_definition_revisions"
# Historical creation-evidence rule: ``store.create`` record version 1 digests
# ``{"resource_type": "agent", "definition": Agent(...).model_dump(mode="json")}``.
STORE_RULE_V1: Final = "store.agent_definition@1"
MAX_REVISION: Final = 10_000

Ref = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[\w.@:-]+$")]
Revision = Annotated[StrictInt, Field(ge=1, le=MAX_REVISION)]


class RevisionError(Exception):
    code = "revision.error"
    retryable = False


class RevisionConflict(RevisionError):
    """The revision identity already holds different content (a competing successor)."""

    code = "revision.conflict"


class RevisionPredecessorMismatch(RevisionError):
    """The exact predecessor revision is missing or has a different digest."""

    code = "revision.predecessor_mismatch"


class RevisionCorrupt(RevisionError):
    code = "revision.corrupt"


class RevisionUnavailable(RevisionError):
    code = "revision.unavailable"
    retryable = True


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def revision_id(tenant_id: str, agent_id: str, revision: int) -> str:
    """Tenant-qualified, unambiguous record identity (no separator parsing)."""
    return sha256_hex(
        {"contract": REVISION_CONTRACT, "tenant": tenant_id, "agent": agent_id, "rev": revision}
    )


class Predecessor(_Frozen):
    revision: Revision
    revision_digest: Digest


class CreationEvidence(_Frozen):
    """The historical ``store.create`` record, copied unchanged (never re-derived)."""

    store_version: Literal[1]
    operation: Literal["create"]
    resource_type: Literal["agent"]
    idempotency_scope: Digest
    request_digest: Digest
    stored_at: datetime
    authorization: AuthorizationProvenance


class BaselineVerificationRecord(_Frozen):
    """How the baseline was established. REV-1 admits only ``verified`` baselines; a
    governed adoption of unverified baselines is a separate, future decision."""

    status: Literal["verified"]
    rule: Literal["store.agent_definition@1"]
    recomputed_request_digest: Digest


class BaselineProvenance(_Frozen):
    creation: CreationEvidence
    verification: BaselineVerificationRecord


class RevisionCorrelation(_Frozen):
    """Bounded correlation with the governed operation that selected this revision."""

    operation_id: Ref | None = None
    correlation_id: Ref | None = None
    causation_id: Ref | None = None


class AgentDefinitionRevision(_Frozen):
    contract: Literal["caos.agent_definition_revision"] = REVISION_CONTRACT
    schema_version: Literal[1] = 1
    revision_id: Digest
    tenant_id: Ref
    agent_id: Ref
    revision: Revision
    origin: Literal["baseline", "revision"]
    predecessor: Predecessor | None = None
    canonical_schema: Literal["caos.agent_definition@1"] = CANONICAL_SCHEMA
    definition: dict[str, Any]
    definition_digest: Digest
    baseline: BaselineProvenance | None = None
    correlation: RevisionCorrelation = Field(default_factory=RevisionCorrelation)
    revision_digest: Digest
    # Insertion time: storage metadata, outside both digests and content identity.
    recorded_at: datetime

    @classmethod
    def build(cls, **fields: Any) -> AgentDefinitionRevision:
        for name, model in (
            ("predecessor", Predecessor),
            ("baseline", BaselineProvenance),
            ("correlation", RevisionCorrelation),
        ):
            if isinstance(fields.get(name), dict):
                fields[name] = model.model_validate(fields[name])
        tenant_id, agent_id = fields["tenant_id"], fields["agent_id"]
        defined = definition_digest(tenant_id, agent_id, fields["definition"])
        unsigned = {
            **fields,
            "revision_id": revision_id(tenant_id, agent_id, fields["revision"]),
            "definition_digest": defined,
        }
        draft = cls.model_construct(**unsigned, revision_digest="0" * 64)
        return cls.model_validate({**unsigned, "revision_digest": draft.chained_digest()})

    def chained_digest(self) -> str:
        """Binds identity, position, exact predecessor, definition and baseline."""
        return sha256_hex(
            {
                "contract": self.contract,
                "schema_version": self.schema_version,
                "tenant_id": self.tenant_id,
                "agent_id": self.agent_id,
                "revision": self.revision,
                "origin": self.origin,
                "predecessor": _dump(self.predecessor),
                "canonical_schema": self.canonical_schema,
                "definition_digest": self.definition_digest,
                "baseline": _dump(self.baseline),
            }
        )

    def content(self) -> dict[str, Any]:
        """Immutable content identity (everything except insertion time)."""
        return self.model_dump(mode="json", exclude={"recorded_at"})

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        if self.revision_id != revision_id(self.tenant_id, self.agent_id, self.revision):
            raise ValueError("Revision identity mismatch")
        if set(self.definition) != CANONICAL_V1_FIELDS:
            raise ValueError("Definition is not a complete canonical v1 definition")
        if (self.definition.get("id"), self.definition.get("tenant_id")) != (
            self.agent_id,
            self.tenant_id,
        ):
            raise ValueError("Definition identity differs from the revision identity")
        if self.definition_digest != definition_digest(
            self.tenant_id, self.agent_id, self.definition
        ):
            raise ValueError("Definition digest mismatch")
        baseline = self.revision == 1
        if baseline != (self.origin == "baseline"):
            raise ValueError("Exactly revision 1 is the creation baseline")
        if baseline != (self.predecessor is None) or baseline != (self.baseline is not None):
            raise ValueError("A baseline has provenance and no predecessor; others the reverse")
        if self.predecessor is not None and self.predecessor.revision != self.revision - 1:
            raise ValueError("The predecessor is exactly the previous revision")
        if self.revision_digest != self.chained_digest():
            raise ValueError("Revision digest mismatch")
        return self


def _dump(value: BaseModel | None) -> dict[str, Any] | None:
    return None if value is None else value.model_dump(mode="json")
