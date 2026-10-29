from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.genesis.initiation import SIGNAL
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    SystemCitizen,
)
from src.domains.agentverse.dependencies.genesis_local import (
    CITIZENS,
    ENKI,
    ENKI_HANDOFF,
    ENKI_PROVISION,
    GENESIS,
    JOSHUA,
    OWNER,
    TENANT,
    initialize_local,
    require_local,
)
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


CHAT = ["chat.context", "chat.response"]
LOCAL_GRANTS = {
    GENESIS: ["data.validation", "store.create.provisioned"],
    ENKI: CHAT,
    JOSHUA: [SIGNAL],
}


def local_container(
    *,
    grants=None,
    approvals=None,
    provisions=None,
    handoffs=None,
    existing=None,
    lines=(),
    citizens=None,
    current=None,
):
    grants = LOCAL_GRANTS if grants is None else grants
    policy = SynapticAuthorization(
        grants={(TENANT, agent): SynapticGrants(invoke) for agent, invoke in grants.items()},
        trusted_lines=lines,
    )
    approvals = (
        {(TENANT, a): AgentActivationApproval(TENANT, a, OWNER, True) for a in grants}
        if approvals is None
        else approvals
    )
    store = SimpleNamespace(current_policy=AsyncMock(return_value=current))
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
        genesis_handoffs=lambda: (ENKI_HANDOFF,) if handoffs is None else handoffs,
        genesis_citizens=lambda: dict(CITIZENS) if citizens is None else citizens,
        db_service=lambda: definitions,
        utu_authority=lambda: utu,
    )
    return SimpleNamespace(agentverse=av), definitions, utu


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "same", "changed"])
async def test_local_bootstrap_seeds_nothing_and_installs_policy_by_cas(local, mode):
    policy = SynapticAuthorization(
        grants={(TENANT, a): SynapticGrants(invoke) for a, invoke in LOCAL_GRANTS.items()}
    )
    expected = {
        "missing": None,
        "same": "incarnation:" + policy_version(policy),
        "changed": "old:digest",
    }[mode]
    container, definitions, utu = local_container(current=expected)
    await initialize_local(container)
    # No Adam bootstrap; Enki only through Genesis; citizens never coll_agents.
    definitions.repository.create.assert_not_awaited()
    definitions.repository.update.assert_not_awaited()
    if mode == "same":
        utu.install_policy.assert_not_awaited()
    else:
        utu.install_policy.assert_awaited_once()
        assert utu.install_policy.call_args.kwargs["expected"] == expected
    # Any CAS failure propagates; there is no unconditional overwrite fallback.
    if mode == "changed":
        utu.install_policy.side_effect = EventPermissionError("conflict")
        with pytest.raises(EventPermissionError):
            await initialize_local(container)


LEGACY_ADAM = "00000000-0000-4000-8000-000000000002"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"grants": {**LOCAL_GRANTS, LEGACY_ADAM: CHAT}},  # legacy Adam bootstrap policy
        {"grants": {**LOCAL_GRANTS, GENESIS: [*LOCAL_GRANTS[GENESIS], "store.create"]}},
        {"grants": {**LOCAL_GRANTS, GENESIS: ["data.validation"]}},
        {"grants": {**LOCAL_GRANTS, ENKI: ["chat.context", "store.create.provisioned"]}},
        {"grants": {ENKI: CHAT, JOSHUA: [SIGNAL]}},  # Enki without the Genesis provisioner
        {"grants": {GENESIS: LOCAL_GRANTS[GENESIS], ENKI: CHAT}},  # no JOSHUA initiator
        {"grants": {**LOCAL_GRANTS, "extra-agent": CHAT}},
        {"provisions": {}},
        {"provisions": {"enki": ENKI_PROVISION.model_copy(update={"principal_id": JOSHUA})}},
        {"provisions": {"enki": ENKI_PROVISION, "other": ENKI_PROVISION}},
        {"handoffs": ()},
        {
            "approvals": {
                (TENANT, a): AgentActivationApproval(TENANT, a, "other", True) for a in LOCAL_GRANTS
            }
        },
        {"lines": (TrustedLine("chat.context", "store.create.provisioned"),)},
        {"citizens": {}},
        {"citizens": {(TENANT, GENESIS): CITIZENS[(TENANT, GENESIS)]}},  # JOSHUA missing
        {"citizens": {**CITIZENS, (TENANT, GENESIS): SystemCitizen(TENANT, GENESIS, "Other")}},
        {"citizens": {**CITIZENS, (TENANT, ENKI): SystemCitizen(TENANT, ENKI, "Enki")}},
    ],
)
async def test_local_policy_must_be_exact(local, change):
    container, definitions, utu = local_container(**change)
    with pytest.raises(ValueError):
        await initialize_local(container)
    definitions.repository.create.assert_not_awaited()
    utu.install_policy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("broaden", ["store.create", "store.create.provisioned", "data.validation"])
async def test_joshua_holds_only_the_initiation_grant(local, broaden):
    container, _, _ = local_container(grants={**LOCAL_GRANTS, JOSHUA: [SIGNAL, broaden]})
    with pytest.raises(ValueError):
        await initialize_local(container)


@pytest.mark.asyncio
async def test_existing_citizen_definition_must_be_the_trusted_seed(local):
    rogue = {"id": GENESIS, "creator": "someone", "tenant_id": TENANT, "runtime_version": "v2"}
    container, _, _ = local_container(existing={GENESIS: rogue})
    with pytest.raises(ValueError, match="Genesis"):
        await initialize_local(container)
    seeded = {**rogue, "creator": OWNER, "genesis_local_seed": True}
    container, definitions, _ = local_container(existing={GENESIS: seeded})
    await initialize_local(container)
    definitions.repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_session_admits_exactly_the_operators_enabled_approvals(local):
    from fastapi import HTTPException

    from src.domains.agentverse.interfaces.api.v2.genesis_sessions import provision_local

    def request(approvals, definitions):
        sessions = SimpleNamespace(provision=AsyncMock(return_value="sid"))
        jwt = SimpleNamespace(verify_token=AsyncMock(return_value={"tid": TENANT, "sub": OWNER}))
        lookup = AsyncMock(side_effect=lambda agent: definitions.get(agent))
        av = SimpleNamespace(
            genesis_approvals=lambda: approvals,
            genesis_citizens=lambda: dict(CITIZENS),
            db_service=lambda: SimpleNamespace(find_by_id=lookup),
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
    approvals = {(TENANT, a): AgentActivationApproval(TENANT, a, OWNER, True) for a in LOCAL_GRANTS}
    approvals[(TENANT, "disabled")] = AgentActivationApproval(TENANT, "disabled", OWNER, False)
    approvals[(TENANT, "foreign")] = AgentActivationApproval(TENANT, "foreign", "other", True)
    # Empty coll_agents: Enki is not provisioned yet but admitted (activation needs
    # its definition). Citizens are admitted by approval with no definition lookup.
    req, sessions = request(approvals, {})
    assert await provision_local(req) == {"session_id": "sid", "tenant_id": TENANT}
    assert sessions.provision.call_args.kwargs["allowed_agent_ids"] == frozenset(
        {GENESIS, ENKI, JOSHUA}
    )
    lookup = req.app.container.agentverse.db_service().find_by_id
    assert {c.args[0] for c in lookup.call_args_list} == {ENKI}
    # A provisioned agent owned by someone else blocks the whole session.
    req, _ = request(approvals, {ENKI: {**owned, "creator": "x"}})
    with pytest.raises(HTTPException):
        await provision_local(req)
    # The operator's JOSHUA approval anchors local membership.
    del approvals[(TENANT, JOSHUA)]
    req, _ = request(approvals, {})
    with pytest.raises(HTTPException):
        await provision_local(req)


# --- G2.1 Recursive Genesis: Enki -> Genesis -> Thoth -----------------------------------

from src.domains.agentverse.agents.genesis.initiation import THOTH_SIGNAL
from src.domains.agentverse.dependencies.genesis_local import (
    THOTH,
    THOTH_HANDOFF,
    THOTH_PROVISION,
)

G21_GRANTS = {**LOCAL_GRANTS, ENKI: [*CHAT, THOTH_SIGNAL], THOTH: CHAT}
G21 = {
    "grants": G21_GRANTS,
    "provisions": {"enki": ENKI_PROVISION, "thoth": THOTH_PROVISION},
    "handoffs": (ENKI_HANDOFF, THOTH_HANDOFF),
}


@pytest.mark.asyncio
async def test_g21_policy_is_accepted_and_seeds_nothing(local):
    container, definitions, _ = local_container(**G21)
    await initialize_local(container)
    definitions.repository.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        # Enki never receives Store, validation or the Enki contract.
        {"grants": {**G21_GRANTS, ENKI: [*CHAT, THOTH_SIGNAL, "store.create.provisioned"]}},
        {"grants": {**G21_GRANTS, ENKI: [*CHAT, THOTH_SIGNAL, "data.validation"]}},
        {"grants": {**G21_GRANTS, ENKI: [*CHAT, THOTH_SIGNAL, SIGNAL]}},
        # JOSHUA's contract is not extended to Thoth; Thoth holds only conversation.
        {"grants": {**G21_GRANTS, JOSHUA: [SIGNAL, THOTH_SIGNAL]}},
        {"grants": {**G21_GRANTS, THOTH: [*CHAT, THOTH_SIGNAL]}},
        {"grants": {**G21_GRANTS, ENKI: CHAT}},  # Thoth pinned without a requester
        {"handoffs": (ENKI_HANDOFF,)},
        {"handoffs": (ENKI_HANDOFF, THOTH_HANDOFF.model_copy(update={"initiator_id": JOSHUA}))},
        {"handoffs": (ENKI_HANDOFF, THOTH_HANDOFF.model_copy(update={"activation": "none"}))},
        {"provisions": {"enki": ENKI_PROVISION}},
        {
            "provisions": {
                "enki": ENKI_PROVISION,
                "thoth": THOTH_PROVISION.model_copy(update={"role_name": "enki"}),
            }
        },
    ],
)
async def test_g21_policy_must_be_exact(local, change):
    container, definitions, utu = local_container(**{**G21, **change})
    with pytest.raises(ValueError):
        await initialize_local(container)
    definitions.repository.create.assert_not_awaited()
    utu.install_policy.assert_not_awaited()
