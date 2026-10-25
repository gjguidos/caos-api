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
from src.domains.agentverse.ncsc.models import SynapticGrants
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
        repository=SimpleNamespace(create=AsyncMock()),
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
    assert document["tenant_id"] == TENANT
    if mode == "same":
        utu.install_policy.assert_not_awaited()
    else:
        utu.install_policy.assert_awaited_once_with(TENANT, expected=expected, authorization=policy)
    # Any CAS failure propagates; there is no unconditional overwrite fallback.
    if mode == "changed":
        utu.install_policy.side_effect = EventPermissionError("conflict")
        with pytest.raises(EventPermissionError):
            await initialize_local(container)
