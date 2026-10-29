"""Explicit local development membership and policy control plane."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.base.dependencies.di_container import Container

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.genesis.initiation import SIGNAL, GenesisHandoff
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import SystemCitizen
from src.domains.agentverse.security.revocation import policy_version
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity

TENANT = "caos-local"
OWNER = "00000000-0000-4000-8000-000000000001"
# The Genesis provisioner principal and the policy-pinned Enki identity.
GENESIS = "00000000-0000-4000-8000-000000000003"
ENKI = "00000000-0000-4000-8000-000000000004"
JOSHUA = "00000000-0000-4000-8000-000000000005"
ENKI_HANDOFF = GenesisHandoff(
    tenant_id=TENANT,
    subject_id=OWNER,
    initiator_id=JOSHUA,
    executor_id=GENESIS,
    agent_id=ENKI,
    slot="enki",
)

_CHAT_GRANTS = (frozenset({"chat.context"}), frozenset({"chat.context", "chat.response"}))
_GENESIS_GRANTS = frozenset({"data.validation", "store.create.provisioned"})
ENKI_PROVISION = ProvisionedIdentity(
    slot="enki",
    resource_type="agent",
    agent_id=ENKI,
    tenant_id=TENANT,
    principal_id=GENESIS,
    subject_id=OWNER,
    name="Enki",
    type="form",
    runtime_version="v2",
)
# The only local policy shape: JOSHUA -> Genesis -> Enki. Nothing is seeded.
_LOCAL = frozenset({(TENANT, GENESIS), (TENANT, ENKI), (TENANT, JOSHUA)})
# Deterministic System Citizens: resolved from trusted policy, never coll_agents.
CITIZENS = {
    (TENANT, GENESIS): SystemCitizen(TENANT, GENESIS, "Genesis"),
    (TENANT, JOSHUA): SystemCitizen(TENANT, JOSHUA, "JOSHUA"),
}


def require_local() -> None:
    if not (
        os.getenv("GENESIS_LOCAL_ENABLED") == "true"
        and os.getenv("GENESIS_SESSIONS_ENABLED") == "true"
        and os.getenv("AUTH_MODE") == "local"
        and os.getenv("ENVIRONMENT") in {"local", "development"}
    ):
        raise EventPermissionError("Genesis local development is disabled")


async def initialize_local(container: Container) -> None:
    require_local()
    av = container.agentverse
    policy = av.synaptic_authorization()
    approvals = av.genesis_approvals()
    scopes = frozenset(approvals)
    if scopes != _LOCAL or set(policy._grants) != scopes:
        raise ValueError("Local Genesis requires exactly the JOSHUA, Genesis and Enki policy")
    for approval in approvals.values():
        if approval.owner_user_id != OWNER or not approval.enabled:
            raise ValueError("Local approval identity mismatch")
    if policy._trusted_lines:
        raise ValueError("Local Genesis policy allows no trusted lines")
    allowed = {GENESIS: (_GENESIS_GRANTS,), JOSHUA: (frozenset({SIGNAL}),), ENKI: _CHAT_GRANTS}
    for _, agent in scopes:
        if policy._grants[(TENANT, agent)].invoke not in allowed[agent]:
            raise ValueError("Local agent grants differ from the explicit local policy")
    if dict(av.genesis_provisions()) != {"enki": ENKI_PROVISION}:
        raise ValueError("Local Genesis requires exactly the pinned Enki provisioned identity")
    if tuple(av.genesis_handoffs()) != (ENKI_HANDOFF,):
        raise ValueError("Local Genesis requires exactly the pinned JOSHUA handoff")
    if dict(av.genesis_citizens()) != CITIZENS:
        raise ValueError("Local policy requires exactly the pinned system citizens")
    definitions = av.db_service()
    for citizen in CITIZENS.values():
        # Enki is created only through Genesis; citizens are never seeded.
        await _check_legacy_citizen(definitions, citizen)
    store = av.utu_authority()._store()
    expected = await store.current_policy(TENANT)
    if expected is None or expected.split(":")[-1] != policy_version(policy):
        await av.utu_authority().install_policy(TENANT, expected=expected, authorization=policy)


async def _check_legacy_citizen(definitions: Any, citizen: SystemCitizen) -> None:
    """Tolerate, never create or delete, a historical local seed for a citizen.

    Resolution ignores it. A record that is not the trusted seed still blocks
    startup: it could be mistaken for a Cognitive Agent elsewhere.
    """
    definition = await definitions.find_by_id(citizen.agent_id)
    if definition is not None and (
        definition.get("creator") != OWNER
        or definition.get("tenant_id") != TENANT
        or definition.get("runtime_version") != "v2"
        or definition.get("genesis_local_seed") is not True
    ):
        raise ValueError(
            f"Existing {citizen.name} definition conflicts with the trusted local seed"
        )
