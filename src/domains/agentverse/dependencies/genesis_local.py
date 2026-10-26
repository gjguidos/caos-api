"""Explicit local development membership and policy control plane."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.base.dependencies.di_container import Container

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.security.revocation import policy_version

TENANT = "caos-local"
OWNER = "00000000-0000-4000-8000-000000000001"
ADAM = "00000000-0000-4000-8000-000000000002"


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
    if set(approvals) != {(TENANT, ADAM)} or set(policy._grants) != {(TENANT, ADAM)}:
        raise ValueError("Local Genesis requires the explicit Adam-only policy")
    approval = approvals[(TENANT, ADAM)]
    if approval.owner_user_id != OWNER or not approval.enabled:
        raise ValueError("Local Adam approval identity mismatch")
    if (
        policy._grants[(TENANT, ADAM)].invoke
        not in (frozenset({"chat.context"}), frozenset({"chat.context", "chat.response"}))
        or policy._trusted_lines
    ):
        raise ValueError("Local Adam requires explicit chat capabilities and no trusted lines")
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
    store = av.utu_authority()._store()
    expected = await store.current_policy(TENANT)
    if expected is None or expected.split(":")[-1] != policy_version(policy):
        await av.utu_authority().install_policy(TENANT, expected=expected, authorization=policy)
