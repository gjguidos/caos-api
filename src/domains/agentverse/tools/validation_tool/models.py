"""Typed ``data.validation`` contracts. Validation is a verdict, never an authorization."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from src.base.entities.user import User
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.entities.agent_request import NAME_PATTERN, RESERVED_NAMES

ValidationResourceType = Literal["agent"]

# Fields the server derives during composition/persistence. A creation input that
# carries one is rejected with a dedicated code, never silently stripped.
SERVER_CONTROLLED_FIELDS = frozenset(
    {
        "id",
        "creator",
        "user_id",
        "ownership",
        "tenant_id",
        "tenant_scoped",
        "exposure",
        "runtime_version",
        "genesis_local_seed",
        "system_name",
        "chat_url",
        "dna_sequence",
        "created_at",
        "updated_at",
        "created",
        "modified",
        "store",
    }
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ValidationRequest(BaseModel):
    """Caller envelope. Requester, tenant and authority are never read from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: ValidationResourceType
    payload: dict[str, Any]


class ToolRefInput(BaseModel):
    """Strict counterpart of ``AgentToolRef``: unknown fields and non-dict config fail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: NonBlank
    config: dict[str, Any] = Field(default_factory=dict)


class AgentCreationInput(BaseModel):
    """Strict Agent Creation Input. Excludes every server-owned ``Agent`` field.

    Back-end selectors (``llm_type`` …) are composition decisions and are not part
    of this contract; the result reports the backend kinds the tools require.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=100, pattern=NAME_PATTERN
        ),
    ]
    type: NonBlank
    role_name: NonBlank
    personality_key: NonBlank
    prompt: NonBlank
    visibility: Literal["public", "private"] = "private"
    whitelisted_groups: list[NonBlank] = Field(default_factory=list)
    tools: list[ToolRefInput] = Field(default_factory=list)
    workflows: list[NonBlank] = Field(default_factory=list)
    rule_pack_ids: list[NonBlank] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _collapse_spaces_and_reserved(cls, value: str) -> str:
        # Same rule as AgentRequest, so V1 and V2 inputs normalize identically.
        value = re.sub(r"\s+", " ", value)
        if value.lower() in RESERVED_NAMES:
            raise ValueError(f"'{value}' is a reserved system name")
        return value


class ValidationIssue(BaseModel):
    """One machine-readable finding. Messages never echo submitted values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    code: str
    message: str


def validation_digest(
    resource_type: str, normalized: dict[str, Any], *, tenant_id: str | None, subject_id: str | None
) -> str:
    """Digest binding a normalized input to the verified requester it was validated for.

    Reference readability is requester-specific (private and tenant resources), so a
    result must not be reused for another subject. Integrity evidence only: it carries
    no secret and is neither an approval nor a grant.
    """
    return sha256_hex(
        {
            "resource_type": resource_type,
            "requester": {"tenant_id": tenant_id, "subject_id": subject_id},
            "normalized": normalized,
        }
    )


class ValidationResult(BaseModel):
    """Deterministic verdict. ``normalized``/``resolved`` are present only when valid.

    ``normalized_digest`` (see :func:`validation_digest`) binds a later stage to the
    exact normalized input and the requester it was validated for.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    resource_type: ValidationResourceType | None
    normalized: dict[str, Any] | None = None
    normalized_digest: str | None = None
    resolved: dict[str, Any] | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class ResourceValidator(Protocol):
    """Read-only validator for one logical resource type; never writes or constructs."""

    resource_type: ValidationResourceType

    async def validate(self, payload: dict[str, Any], *, requester: User) -> ValidationResult: ...
