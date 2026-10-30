"""Trusted bootstrap inputs for the deterministic UTU system authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from src.domains.agentverse.agents.genesis.bootstrap import GenesisBootstrap
from src.domains.agentverse.agents.genesis.initiation import GenesisHandoff
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    RevocableAgentSessionAuthority,
    SystemCitizen,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.security.validation_gateway import ValidationGatewayBinding
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity


@dataclass(frozen=True)
class GenesisSecurityBindings:
    # Session invalidation MUST call the shared UTUAuthority.revoke(session_id=...).
    # Provision each tenant with UTUAuthority.install_policy before activation.
    # Construct only from verified server policy/configuration, never a request.
    sessions: RevocableAgentSessionAuthority
    authorization: SynapticAuthorization
    approvals: Mapping[tuple[str, str], AgentActivationApproval] = field(default_factory=dict)
    # Policy-pinned identities for store.create.provisioned, keyed by slot.
    provisions: Mapping[str, ProvisionedIdentity] = field(default_factory=dict)
    handoffs: tuple[GenesisHandoff, ...] = ()
    # Deterministic system principals resolved without a coll_agents definition.
    citizens: Mapping[tuple[str, str], SystemCitizen] = field(default_factory=dict)
    # Policy-scoped startup leases; each may only initiate its pinned handoff.
    bootstrap: tuple[GenesisBootstrap, ...] = ()
    validation_gateways: tuple[ValidationGatewayBinding, ...] = ()
    # Reviewed (tenant, user, source Agent) → executor bindings for governed creation.
    creation_gateways: tuple[ValidationGatewayBinding, ...] = ()
