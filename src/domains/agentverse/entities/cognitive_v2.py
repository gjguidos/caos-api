"""Declarative AOSA cognitive execution contract. No executor or memory store.

Profiles describe configuration, not authority. Only UTU-issued principals and
CPBX may authorize execution; no serialized model or capability list grants it.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domains.agentverse.entities.conversation_v2 import Identifier
from src.domains.agentverse.entities.history_v2 import AgentHistoryViewV2
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal


class ImmutableContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentCognitiveProfile(ImmutableContract):
    agent_id: Identifier
    definition_version: str = Field(min_length=1)
    type: str = Field(min_length=1)
    role: str = Field(min_length=1)
    personality: str | None = None
    rule_packs: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    # Declarative identifiers only; never runtime resource handles or credentials.
    cognitive_resources: tuple[str, ...] = ()


class CognitiveTurn(ImmutableContract):
    id: Identifier
    tenant_id: Identifier
    conversation_id: Identifier
    user_id: Identifier
    agent_id: Identifier
    message: str = Field(min_length=1, max_length=8192)
    correlation_id: Identifier
    causation_id: Identifier


@dataclass(frozen=True)
class CognitiveContext:
    """Trusted assembly only, never accepted from a client JSON envelope."""

    profile: AgentCognitiveProfile
    turn: CognitiveTurn
    history: AgentHistoryViewV2
    principal: AuthorizedAgentPrincipal
    authorized_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.principal, AuthorizedAgentPrincipal):
            raise TypeError("UTU principal required")
        if not isinstance(self.authorized_capabilities, tuple):
            raise TypeError("Authorized capabilities must be an immutable tuple")
        key = self.principal.permit.key
        if (
            self.profile.agent_id != self.turn.agent_id
            or self.history.agent_id != self.turn.agent_id
            or (self.principal.tenant_id, self.principal.agent_id)
            != (self.turn.tenant_id, self.turn.agent_id)
            or (key.tenant_id, key.agent_id, key.user_id)
            != (self.turn.tenant_id, self.turn.agent_id, self.turn.user_id)
            or any(
                (m.tenant_id, m.conversation_id) != (self.turn.tenant_id, self.turn.conversation_id)
                or (m.turn_user_id == self.turn.user_id and m.event_id == self.turn.id)
                or (m.visible_user_ids and self.turn.user_id not in m.visible_user_ids)
                for m in self.history.messages
            )
            or not set(self.authorized_capabilities) <= set(self.profile.capabilities)
        ):
            raise ValueError("Cognitive context identity or capability mismatch")
        # These are a snapshot only. CPBX must recheck the live permit per invocation.


class CognitiveResult(ImmutableContract):
    turn_id: Identifier
    agent_id: Identifier
    kind: Literal["agent_response", "capability_contribution", "internal_event"]
    content: str = Field(max_length=65536)
    capability: str | None = None

    @model_validator(mode="after")
    def provenance(self) -> "CognitiveResult":
        if (self.kind == "capability_contribution") != (self.capability is not None):
            raise ValueError("Capability output must retain capability provenance")
        return self
