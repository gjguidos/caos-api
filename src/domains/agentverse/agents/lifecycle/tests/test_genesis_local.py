from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import AgentActivationApproval
from src.domains.agentverse.dependencies.genesis_local import (
    ADAM,
    OWNER,
    TENANT,
    initialize_local,
    require_local,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.security.revocation import policy_version


@pytest.fixture
def local(monkeypatch):
    for key, value in {
        "GENESIS_LOCAL_ENABLED": "true",
        "GENESIS_SESSIONS_ENABLED": "true",
        "AUTH_MODE": "local",
        "ENVIRONMENT": "development",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("ENVIRONMENT", "production"),
        ("AUTH_MODE", "entra"),
        ("GENESIS_LOCAL_ENABLED", "false"),
        ("GENESIS_SESSIONS_ENABLED", "false"),
    ],
)
def test_local_requires_all_development_gates(local, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(EventPermissionError):
        require_local()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "same", "changed"])
async def test_trusted_seed_and_policy_cas(local, mode):
    policy = SynapticAuthorization(grants={(TENANT, ADAM): SynapticGrants(["chat.context"])})
    expected = {
        "missing": None,
        "same": "incarnation:" + policy_version(policy),
        "changed": "old:digest",
    }[mode]
    store = SimpleNamespace(current_policy=AsyncMock(return_value=expected))
    utu = SimpleNamespace(_store=lambda: store, install_policy=AsyncMock())
    definitions = SimpleNamespace(
        find_by_id=AsyncMock(return_value=None),
        repository=SimpleNamespace(create=AsyncMock(), update=AsyncMock(return_value=True)),
        AGENTS_COL="coll_agents",
    )
    container = SimpleNamespace(
        agentverse=SimpleNamespace(
            synaptic_authorization=lambda: policy,
            genesis_approvals=lambda: {
                (TENANT, ADAM): AgentActivationApproval(TENANT, ADAM, OWNER, True)
            },
            db_service=lambda: definitions,
            utu_authority=lambda: utu,
        )
    )
    await initialize_local(container)
    document = definitions.repository.create.call_args.args[0]
    assert Agent.model_validate(document).visibility == "private"
    assert document["runtime_version"] == "v2"
    assert document["tenant_id"] == TENANT
    assert document["ownership"] == "tenant"
    if mode == "same":
        utu.install_policy.assert_not_awaited()
    else:
        utu.install_policy.assert_awaited_once_with(TENANT, expected=expected, authorization=policy)
    # Any CAS failure propagates; there is no unconditional overwrite fallback.
    if mode == "changed":
        utu.install_policy.side_effect = EventPermissionError("conflict")
        with pytest.raises(EventPermissionError):
            await initialize_local(container)


@pytest.mark.asyncio
@pytest.mark.parametrize("provenance", [True, False])
async def test_existing_seed_requires_persisted_provenance_to_backfill(local, provenance):
    policy = SynapticAuthorization(grants={(TENANT, ADAM): SynapticGrants(["chat.context"])})
    existing = {"id": ADAM, "creator": OWNER, "tenant_id": TENANT, "genesis_local_seed": provenance}
    repository = SimpleNamespace(create=AsyncMock(), update=AsyncMock(return_value=True))
    store = SimpleNamespace(current_policy=AsyncMock(return_value="inc:" + policy_version(policy)))
    utu = SimpleNamespace(_store=lambda: store, install_policy=AsyncMock())
    definitions = SimpleNamespace(
        find_by_id=AsyncMock(return_value=existing), repository=repository, AGENTS_COL="coll_agents"
    )
    container = SimpleNamespace(
        agentverse=SimpleNamespace(
            synaptic_authorization=lambda: policy,
            genesis_approvals=lambda: {
                (TENANT, ADAM): AgentActivationApproval(TENANT, ADAM, OWNER, True)
            },
            db_service=lambda: definitions,
            utu_authority=lambda: utu,
        )
    )
    if provenance:
        await initialize_local(container)
        assert repository.update.call_args.kwargs["query"]["runtime_version"] == {"$exists": False}
        assert repository.update.call_args.kwargs["data"] == {"runtime_version": "v2"}
        repository.update.reset_mock()
        existing["runtime_version"] = "v2"
        await initialize_local(container)
        repository.update.assert_not_awaited()
        existing.pop("runtime_version")
        repository.update.return_value = False
        with pytest.raises(ValueError, match="conflicted"):
            await initialize_local(container)
    else:
        with pytest.raises(ValueError, match="provenance"):
            await initialize_local(container)
        repository.update.assert_not_awaited()
    repository.create.assert_not_awaited()
    utu.install_policy.assert_not_awaited()


# --- G1: Genesis provisioner and policy-pinned Enki ---------------------------------------

from src.domains.agentverse.dependencies.genesis_local import (
    ENKI,
    ENKI_PROVISION,
    GENESIS,
)

CHAT = ["chat.context", "chat.response"]
G1_GRANTS = {ADAM: CHAT, GENESIS: ["data.validation", "store.create.provisioned"], ENKI: CHAT}


def g1_container(*, grants=None, approvals=None, provisions=None, existing=None, lines=()):
    grants = G1_GRANTS if grants is None else grants
    policy = SynapticAuthorization(
        grants={(TENANT, agent): SynapticGrants(invoke) for agent, invoke in grants.items()},
        trusted_lines=lines,
    )
    approvals = (
        {(TENANT, a): AgentActivationApproval(TENANT, a, OWNER, True) for a in grants}
        if approvals is None
        else approvals
    )
    store = SimpleNamespace(current_policy=AsyncMock(return_value=None))
    utu = SimpleNamespace(_store=lambda: store, install_policy=AsyncMock())
    definitions = SimpleNamespace(
        find_by_id=AsyncMock(side_effect=lambda agent: (existing or {}).get(agent)),
        repository=SimpleNamespace(create=AsyncMock(), update=AsyncMock(return_value=True)),
        AGENTS_COL="coll_agents",
    )
    av = SimpleNamespace(
        synaptic_authorization=lambda: policy,
        genesis_approvals=lambda: approvals,
        genesis_provisions=lambda: {"enki": ENKI_PROVISION} if provisions is None else provisions,
        db_service=lambda: definitions,
        utu_authority=lambda: utu,
    )
    return SimpleNamespace(agentverse=av), definitions, utu


@pytest.mark.asyncio
async def test_g1_policy_seeds_adam_and_genesis_but_never_enki(local):
    container, definitions, utu = g1_container()
    await initialize_local(container)
    seeded = {
        call.args[0]["id"]: call.args[0] for call in definitions.repository.create.call_args_list
    }
    assert set(seeded) == {ADAM, GENESIS}
    genesis = Agent.model_validate(seeded[GENESIS])
    assert (genesis.ownership, genesis.tenant_id, genesis.creator) == ("tenant", TENANT, OWNER)
    assert (genesis.runtime_version, genesis.type, genesis.visibility) == (
        "v2",
        "orchestration",
        "private",
    )
    assert seeded[GENESIS]["genesis_local_seed"] is True
    utu.install_policy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"grants": {**G1_GRANTS, GENESIS: [*G1_GRANTS[GENESIS], "store.create"]}},
        {"grants": {**G1_GRANTS, GENESIS: ["data.validation"]}},
        {"grants": {**G1_GRANTS, ENKI: ["chat.context", "store.create.provisioned"]}},
        {"grants": {ADAM: CHAT, ENKI: CHAT}},  # Enki without the Genesis provisioner
        {"grants": {**G1_GRANTS, "extra-agent": CHAT}},
        {"provisions": {}},
        {"provisions": {"enki": ENKI_PROVISION.model_copy(update={"principal_id": ADAM})}},
        {"provisions": {"enki": ENKI_PROVISION, "adam": ENKI_PROVISION}},
        {
            "approvals": {
                (TENANT, a): AgentActivationApproval(TENANT, a, "other", True) for a in G1_GRANTS
            }
        },
        {"lines": (TrustedLine("chat.context", "store.create.provisioned"),)},
    ],
)
async def test_g1_policy_must_be_exact(local, change):
    container, definitions, _ = g1_container(**change)
    with pytest.raises(ValueError):
        await initialize_local(container)
    definitions.repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_genesis_definition_must_be_the_trusted_seed(local):
    adam = {"id": ADAM, "creator": OWNER, "tenant_id": TENANT, "runtime_version": "v2"}
    rogue = {"id": GENESIS, "creator": "someone", "tenant_id": TENANT, "runtime_version": "v2"}
    container, _, _ = g1_container(existing={ADAM: adam, GENESIS: rogue})
    with pytest.raises(ValueError, match="Genesis"):
        await initialize_local(container)
    seeded = {**rogue, "creator": OWNER, "genesis_local_seed": True}
    container, definitions, _ = g1_container(existing={ADAM: adam, GENESIS: seeded})
    await initialize_local(container)
    definitions.repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_session_admits_exactly_the_operators_enabled_approvals(local):
    from fastapi import HTTPException

    from src.domains.agentverse.interfaces.api.v2.genesis_sessions import provision_local

    def request(approvals, definitions):
        sessions = SimpleNamespace(provision=AsyncMock(return_value="sid"))
        jwt = SimpleNamespace(verify_token=AsyncMock(return_value={"tid": TENANT, "sub": OWNER}))
        av = SimpleNamespace(
            genesis_approvals=lambda: approvals,
            db_service=lambda: SimpleNamespace(
                find_by_id=AsyncMock(side_effect=lambda agent: definitions.get(agent))
            ),
        )
        return SimpleNamespace(
            headers={"authorization": "Bearer token"},
            app=SimpleNamespace(
                state=SimpleNamespace(genesis_sessions=sessions),
                container=SimpleNamespace(
                    agentverse=av, services=SimpleNamespace(jwt_service=lambda: jwt)
                ),
            ),
        ), sessions

    owned = {"creator": OWNER, "tenant_id": TENANT}
    approvals = {
        (TENANT, ADAM): AgentActivationApproval(TENANT, ADAM, OWNER, True),
        (TENANT, GENESIS): AgentActivationApproval(TENANT, GENESIS, OWNER, True),
        (TENANT, ENKI): AgentActivationApproval(TENANT, ENKI, OWNER, True),
        (TENANT, "disabled"): AgentActivationApproval(TENANT, "disabled", OWNER, False),
        (TENANT, "foreign"): AgentActivationApproval(TENANT, "foreign", "other", True),
    }
    # Enki is not provisioned yet; it is still admitted (activation needs its definition).
    req, sessions = request(approvals, {ADAM: owned, GENESIS: owned})
    assert await provision_local(req) == {"session_id": "sid", "tenant_id": TENANT}
    assert sessions.provision.call_args.kwargs["allowed_agent_ids"] == frozenset(
        {ADAM, GENESIS, ENKI}
    )
    # A provisioned agent owned by someone else blocks the whole session.
    req, _ = request(approvals, {ADAM: owned, GENESIS: owned, ENKI: {**owned, "creator": "x"}})
    with pytest.raises(HTTPException):
        await provision_local(req)
    # Adam must exist.
    req, _ = request(approvals, {GENESIS: owned})
    with pytest.raises(HTTPException):
        await provision_local(req)
