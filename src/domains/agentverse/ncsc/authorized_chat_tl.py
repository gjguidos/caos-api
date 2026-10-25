
import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.ncsc.authorization import (
    SynapticAuthorization,
)
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import (
    Synapse,
    SynapticGrants,
    TrustedLine,
)
from src.domains.agentverse.registries.synaptic_registry import (
    SynapticRegistry,
)


@pytest.mark.asyncio
async def test_authorized_chat_trusted_line():
    registry = SynapticRegistry()
    observed = []

    @registry.subscribe("llm.generate", provider="tool.llm")
    async def generate(synapse: Synapse, client):
        observed.append(synapse)
        return {"text": "Hello from CAOS"}

    @registry.subscribe("chat.respond", provider="tool.chat")
    async def chat(synapse: Synapse, client):
        return await client.request(
            "llm.generate",
            {"prompt": synapse.payload["message"]},
        )

    authorization = SynapticAuthorization(
        grants={
            ("tenant.alpha", "agent.enki"): SynapticGrants(
                invoke=frozenset({"chat.respond"}),
            ),
        },
        trusted_lines={
            TrustedLine("chat.respond", "llm.generate"),
        },
    )

    exchange = SynapticExchange(
        registry=registry,
        authorization=authorization,
    )

    enki = exchange.bind(
        EventPrincipal("agent.enki", "tenant.alpha")
    )

    # Approved agent → chat → LLM chain.
    result = await enki.request(
        "chat.respond",
        {"message": "Hello"},
    )

    assert result == {"text": "Hello from CAOS"}
    assert len(observed) == 1
    assert observed[0].source == "agent.enki"
    assert observed[0].tenant_id == "tenant.alpha"

    # The agent cannot invoke the LLM directly.
    with pytest.raises(EventPermissionError):
        await enki.request(
            "llm.generate",
            {"prompt": "Bypass chat"},
        )

    # The agent cannot invoke chat in a different tenant.
    other_tenant = exchange.bind(
        EventPrincipal("agent.enki", "tenant.beta")
    )

    with pytest.raises(EventPermissionError):
        await other_tenant.request(
            "chat.respond",
            {"message": "Hello"},
        )
