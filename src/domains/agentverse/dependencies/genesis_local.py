"""Explicit local development membership and policy control plane."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.base.dependencies.di_container import Container

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.security.revocation import policy_version
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity

TENANT = "caos-local"
OWNER = "00000000-0000-4000-8000-000000000001"
ADAM = "00000000-0000-4000-8000-000000000002"
# G1: the Genesis provisioner principal and the policy-pinned Enki identity.
GENESIS = "00000000-0000-4000-8000-000000000003"
ENKI = "00000000-0000-4000-8000-000000000004"

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
    type="chat",
    runtime_version="v2",
)
_ADAM_ONLY = frozenset({(TENANT, ADAM)})
_G1 = frozenset({(TENANT, ADAM), (TENANT, GENESIS), (TENANT, ENKI)})


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
    if scopes not in (_ADAM_ONLY, _G1) or set(policy._grants) != scopes:
        raise ValueError(
            "Local Genesis requires the explicit Adam-only or Adam/Genesis/Enki policy"
        )
    for approval in approvals.values():
        if approval.owner_user_id != OWNER or not approval.enabled:
            raise ValueError("Local approval identity mismatch")
    if policy._trusted_lines:
        raise ValueError("Local Genesis policy allows no trusted lines")
    for _, agent in scopes:
        allowed = (_GENESIS_GRANTS,) if agent == GENESIS else _CHAT_GRANTS
        if policy._grants[(TENANT, agent)].invoke not in allowed:
            raise ValueError("Local agent grants differ from the explicit local policy")
    if scopes == _G1 and dict(av.genesis_provisions()) != {"enki": ENKI_PROVISION}:
        raise ValueError("Local Genesis requires exactly the pinned Enki provisioned identity")
    definitions = av.db_service()
    definition = await definitions.find_by_id(ADAM)
    if definition is None:
        document = Agent(
            ownership="tenant",
            tenant_id=TENANT,
            runtime_version="v2",
            id=ADAM,
            name="Adam",
            system_name="adam",
            creator=OWNER,
            type="chat",
            prompt="You are Adam, a local development agent.",
            chat_url="",
            llm_type="openai",
            audio_type="openai",
            db_type="mongodb",
            cache_type="redis",
            knowledge_db_type="chromadb",
            messaging_type="rabbitmq",
            dna_sequence="local-genesis",
            visibility="private",
        ).model_dump()
        document.update(tenant_id=TENANT, enabled=True, genesis_local_seed=True)
        await definitions.repository.create(document, definitions.AGENTS_COL)
    elif definition.get("creator") != OWNER or definition.get("tenant_id") != TENANT:
        raise ValueError("Existing Adam definition conflicts with local ownership")
    elif definition.get("runtime_version") != "v2":
        # Only this trusted bootstrap's historical seed may be backfilled.
        # A matching name/id/provider or an approval alone is not provenance.
        if "runtime_version" in definition or definition.get("genesis_local_seed") is not True:
            raise ValueError("Existing Adam definition has no trusted V2 runtime provenance")
        updated = await definitions.repository.update(
            query={
                "id": ADAM,
                "creator": OWNER,
                "tenant_id": TENANT,
                "genesis_local_seed": True,
                "runtime_version": {"$exists": False},
            },
            data={"runtime_version": "v2"},
            collection_name=definitions.AGENTS_COL,
        )
        if not updated:
            raise ValueError("Adam runtime migration conflicted; retry bootstrap")
    if scopes == _G1:
        # Enki is never seeded: it is created only through store.create.provisioned.
        await _seed_genesis(definitions)
    store = av.utu_authority()._store()
    expected = await store.current_policy(TENANT)
    if expected is None or expected.split(":")[-1] != policy_version(policy):
        await av.utu_authority().install_policy(TENANT, expected=expected, authorization=policy)


async def _seed_genesis(definitions: Any) -> None:
    """Trusted local seed of the Genesis provisioner principal (same path as Adam).

    It has no chat capability and no backend selectors: it only provisions.
    """
    definition = await definitions.find_by_id(GENESIS)
    if definition is None:
        document = Agent(
            ownership="tenant",
            tenant_id=TENANT,
            runtime_version="v2",
            id=GENESIS,
            name="Genesis",
            system_name="genesis",
            creator=OWNER,
            type="orchestration",
            prompt="Genesis provisions predefined CAOS agents.",
            chat_url="",
            llm_type="",
            audio_type="",
            db_type=None,
            cache_type=None,
            knowledge_db_type=None,
            messaging_type=None,
            dna_sequence="local-genesis",
            visibility="private",
        ).model_dump()
        document.update(tenant_id=TENANT, enabled=True, genesis_local_seed=True)
        await definitions.repository.create(document, definitions.AGENTS_COL)
    elif (
        definition.get("creator") != OWNER
        or definition.get("tenant_id") != TENANT
        or definition.get("runtime_version") != "v2"
        or definition.get("genesis_local_seed") is not True
    ):
        raise ValueError("Existing Genesis definition conflicts with the trusted local seed")
