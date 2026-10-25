import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import (
    EventPrincipal,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse, SynapticGrants
from src.domains.agentverse.registries.synaptic_registry import (
    SynapticRegistry,
)

SIGNAL = "personality.infuse"
PROVIDER = "tool.personality"


def setup_exchange(*, allowed: bool):
    registry = SynapticRegistry()
    received = []

    @registry.subscribe(SIGNAL, provider=PROVIDER)
    async def handler(synapse: Synapse, client):
        received.append(synapse)
        return {"personality": synapse.payload["key"]}

    grants = SynapticGrants(invoke=frozenset({SIGNAL}) if allowed else frozenset())

    policy = SynapticAuthorization(
        grants={
            ("tenant.default", "agent.writer"): grants,
        }
    )

    exchange = SynapticExchange(
        registry=registry,
        authorization=policy,
    )

    client = exchange.bind(
        EventPrincipal(
            agent_id="agent.writer",
            tenant_id="tenant.default",
        )
    )

    return client, received


@pytest.mark.asyncio
async def test_authorized_invocation():
    client, received = setup_exchange(allowed=True)

    result = await client.request(
        SIGNAL,
        {"key": "creative"},
    )

    assert result == {"personality": "creative"}
    assert len(received) == 1
    assert received[0].source == "agent.writer"
    assert received[0].target == PROVIDER
    assert received[0].tenant_id == "tenant.default"


@pytest.mark.asyncio
async def test_invocation_denied():
    client, received = setup_exchange(allowed=False)

    with pytest.raises(EventPermissionError):
        await client.request(SIGNAL, {"key": "creative"})

    assert received == []


@pytest.mark.asyncio
async def test_target_mismatch_denied():
    client, received = setup_exchange(allowed=True)

    with pytest.raises(EventPermissionError):
        await client.request(
            SIGNAL,
            {"key": "creative"},
            target="tool.database",
        )

    assert received == []


@pytest.mark.asyncio
async def test_correlation_preserved():
    client, received = setup_exchange(allowed=True)

    await client.request(
        SIGNAL,
        {"key": "creative"},
        correlation_id="conversation-123",
        causation_id="synapse-parent-456",
    )

    synapse = received[0]

    assert synapse.correlation_id == "conversation-123"
    assert synapse.causation_id == "synapse-parent-456"
    assert synapse.synapse_id


@pytest.mark.asyncio
async def test_unknown_capability():
    client, _ = setup_exchange(allowed=True)

    # Grant explicitly, but do not register a handler.
    registry = SynapticRegistry()
    policy = SynapticAuthorization(
        grants={
            ("tenant.default", "agent.writer"): SynapticGrants(
                invoke=frozenset({"unknown.capability"})
            )
        }
    )
    exchange = SynapticExchange(
        registry=registry,
        authorization=policy,
    )
    client = exchange.bind(EventPrincipal("agent.writer", "tenant.default"))

    with pytest.raises(KeyError):
        await client.request("unknown.capability", {})


@pytest.mark.asyncio
async def test_payload_is_copied():
    client, received = setup_exchange(allowed=True)

    payload = {"key": "creative"}
    await client.request(SIGNAL, payload)

    assert received[0].payload is not payload
    assert received[0].payload == payload
