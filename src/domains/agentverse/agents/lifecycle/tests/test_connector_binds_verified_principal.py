
from types import SimpleNamespace

import pytest

from src.domains.agentverse.agents.lifecycle.ncsc_connector import (
    NCSCConnectionError,
    TrustedNCSCConnector,
)
from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)


class FakeExchange:
    def __init__(self):
        self.bound = []

    def bind(self, principal):
        self.bound.append(principal)
        return object()


@pytest.mark.asyncio
async def test_connector_binds_verified_principal():
    key = AgentInstanceKey(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="adam",
        session_id="session-1",
    )

    principal = SimpleNamespace(
        tenant_id="tenant-1",
        agent_id="adam",
    )

    async def resolve(_key):
        return principal

    exchange = FakeExchange()
    connector = TrustedNCSCConnector(
        exchange=exchange,
        resolve_principal=resolve,
    )

    client = await connector(key)

    assert client is not None
    assert exchange.bound == [principal]


@pytest.mark.asyncio
async def test_connector_rejects_tenant_mismatch():
    key = AgentInstanceKey(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id="adam",
        session_id="session-1",
    )

    async def resolve(_key):
        return SimpleNamespace(
            tenant_id="tenant-2",
            agent_id="adam",
        )

    exchange = FakeExchange()
    connector = TrustedNCSCConnector(
        exchange=exchange,
        resolve_principal=resolve,
    )

    with pytest.raises(NCSCConnectionError):
        await connector(key)

    assert exchange.bound == []
