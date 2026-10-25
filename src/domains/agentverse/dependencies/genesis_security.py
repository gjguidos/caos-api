"""Trusted bootstrap inputs for the deterministic UTU system authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    RevocableAgentSessionAuthority,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization


@dataclass(frozen=True)
class GenesisSecurityBindings:
    # Session invalidation MUST call the shared UTUAuthority.revoke(session_id=...).
    # Provision each tenant with UTUAuthority.install_policy before activation.
    # Construct only from verified server policy/configuration, never a request.
    sessions: RevocableAgentSessionAuthority
    authorization: SynapticAuthorization
    approvals: Mapping[tuple[str, str], AgentActivationApproval] = field(default_factory=dict)
