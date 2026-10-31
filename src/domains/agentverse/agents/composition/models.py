"""Typed V2 Agent Composition contracts. Composition decides shape, never authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domains.agentverse.security.revocation import ExecutionPermit


@dataclass(frozen=True)
class CompositionContext:
    """Trusted subject of the composing execution; constructed only from a verified permit.

    It is the same identity ``store.create`` derives ``creator``/``tenant_id`` from, so
    composition never needs (or accepts) them from a payload.
    """

    tenant_id: str
    subject_id: str

    @classmethod
    def from_permit(cls, permit: ExecutionPermit) -> CompositionContext:
        if not isinstance(permit, ExecutionPermit):
            raise TypeError("Composition requires a verified execution permit")
        return cls(tenant_id=permit.key.tenant_id, subject_id=permit.key.user_id)


class V2CompositionPolicy(BaseModel):
    """Explicit, reviewed V2 restrictions. Every field is required: no implicit defaults.

    Mirrors what the current V2 runtime (``CognitiveResponseV2``) can execute.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_version: Literal["v2"]
    agent_types: frozenset[str] = Field(min_length=1)
    required_backends: frozenset[str]
    role_tools_supported: bool
    # Reviewed (type, role) creation compositions that may be created without an explicit
    # ``conversation@1.0.0`` attachment and receive the default Conversation presentation.
    # Creation shape and presentation only (AGENT-DEF-SEC-0.1): a definition created this
    # way does NOT converse until the Tool is attached (or its identity is a reviewed
    # legacy definition). A missing role is ``general``.
    creation_compositions: frozenset[tuple[str, str]]

    def creatable(self, agent_type: str | None, role_name: str | None) -> bool:
        return (agent_type or "", role_name or "general") in self.creation_compositions


# Chat and the explicitly approved form/enki composition; no role-tool execution. The
# creation compositions are those accepted at CONVERSATION-0.1A (unchanged outcomes).
CURRENT_V2_POLICY = V2CompositionPolicy(
    runtime_version="v2",
    agent_types=frozenset({"chat", "form"}),
    required_backends=frozenset({"llm"}),
    role_tools_supported=False,
    creation_compositions=frozenset({("chat", "general"), ("chat", "thoth"), ("form", "enki")}),
)


class CompositionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    code: str
    message: str


class CompositionResult(BaseModel):
    """``candidate`` is an ``Agent``-compatible ``store.create`` payload, present only
    when composed. It never contains identity, ownership, tenant, creator or exposure:
    ``store.create`` derives those from its own trusted write context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    composed: bool
    candidate: dict[str, Any] | None = None
    validation_digest: str | None = None
    issues: list[CompositionIssue] = Field(default_factory=list)
