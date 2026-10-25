from dataclasses import replace

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventGrants, EventPrincipal
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import (
    SynapticExecutionContext,
    SynapticGrants,
    TrustedLine,
)
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry

ROOT = "chat.respond"
MIDDLE = "prompt.build"
LEAF = "llm.generate"
KEY = ("tenant.alpha", "agent.enki")


def policy(*, lines=(), grants=None):
    return SynapticAuthorization(
        grants={KEY: SynapticGrants(invoke={ROOT})} if grants is None else grants,
        trusted_lines=lines,
    )


def context(**kwargs):
    return SynapticExecutionContext(tenant_id=KEY[0], principal_id=KEY[1], **kwargs)


@pytest.mark.parametrize("tenant,principal", [KEY, ("tenant.beta", KEY[1]), (KEY[0], "other")])
def test_root_grants_are_tenant_and_principal_scoped(tenant, principal):
    auth = policy()
    ctx = SynapticExecutionContext(principal, tenant)
    if (tenant, principal) == KEY:
        auth.require_invoke(context=ctx, signal=ROOT)
    else:
        with pytest.raises(EventPermissionError):
            auth.require_invoke(context=ctx, signal=ROOT)


def test_missing_grants_and_wildcards_deny():
    for grants in ({}, {KEY: SynapticGrants()}, {KEY: SynapticGrants(invoke={"*"})}):
        with pytest.raises(EventPermissionError):
            policy(grants=grants).require_invoke(context=context(), signal=ROOT)


def test_broker_publish_or_subscribe_grants_cannot_authorize_invocation():
    with pytest.raises(TypeError):
        SynapticAuthorization(grants={KEY: EventGrants(publish={ROOT}, subscribe={ROOT})})


def test_policy_snapshots_mutable_configuration():
    signals = {ROOT}
    grants = {KEY: SynapticGrants(invoke=signals)}
    lines = {TrustedLine(ROOT, LEAF)}
    auth = policy(grants=grants, lines=lines)
    signals.clear()
    grants.clear()
    lines.clear()
    auth.require_invoke(context=context(active_signal=ROOT, invocation_chain=(ROOT,)), signal=LEAF)


@pytest.mark.parametrize(
    "ctx",
    [
        context(active_signal=ROOT),
        context(active_signal=ROOT, invocation_chain=(MIDDLE,)),
        context(active_signal=MIDDLE, invocation_chain=("other.root", MIDDLE)),
        replace(context(active_signal=ROOT, invocation_chain=(ROOT,)), tenant_id="tenant.beta"),
        replace(context(active_signal=ROOT, invocation_chain=(ROOT,)), principal_id="other"),
    ],
)
def test_trusted_line_cannot_bypass_root_identity_or_chain(ctx):
    auth = policy(lines={TrustedLine(ROOT, LEAF), TrustedLine(MIDDLE, LEAF)})
    with pytest.raises(EventPermissionError):
        auth.require_invoke(context=ctx, signal=LEAF)


def test_every_edge_requires_a_trusted_line_even_with_direct_leaf_grant():
    ctx = context(active_signal=MIDDLE, invocation_chain=(ROOT, MIDDLE))
    auth = policy(lines={TrustedLine(MIDDLE, LEAF)})
    with pytest.raises(EventPermissionError):
        auth.require_invoke(context=ctx, signal=LEAF)
    auth = policy(grants={KEY: SynapticGrants(invoke={ROOT, LEAF})})
    with pytest.raises(EventPermissionError):
        auth.require_invoke(
            context=context(active_signal=ROOT, invocation_chain=(ROOT,)), signal=LEAF
        )


@pytest.mark.parametrize(
    "tenant,principal", [(None, None), KEY, ("tenant.beta", None), (None, "other")]
)
def test_trusted_line_optional_scopes(tenant, principal):
    auth = policy(lines={TrustedLine(ROOT, LEAF, tenant_id=tenant, principal_id=principal)})
    ctx = context(active_signal=ROOT, invocation_chain=(ROOT,))
    if tenant in (None, KEY[0]) and principal in (None, KEY[1]):
        auth.require_invoke(context=ctx, signal=LEAF)
    else:
        with pytest.raises(EventPermissionError):
            auth.require_invoke(context=ctx, signal=LEAF)


@pytest.mark.asyncio
async def test_multihop_delegation_preserves_identity_payload_and_lineage():
    registry = SynapticRegistry()
    observed = []

    @registry.subscribe(ROOT, provider="tool.chat")
    async def root(synapse, client):
        observed.append(synapse)
        return await client.request(
            MIDDLE, synapse.payload, correlation_id="spoof", causation_id="spoof"
        )

    @registry.subscribe(MIDDLE, provider="tool.prompt")
    async def middle(synapse, client):
        observed.append(synapse)
        return await client.request(
            LEAF, synapse.payload, correlation_id="spoof", causation_id="spoof"
        )

    @registry.subscribe(LEAF, provider="tool.llm")
    async def leaf(synapse, client):
        observed.append(synapse)
        synapse.payload["nested"]["value"] = "changed"
        return "ok"

    exchange = SynapticExchange(
        registry=registry,
        authorization=policy(lines={TrustedLine(ROOT, MIDDLE), TrustedLine(MIDDLE, LEAF)}),
    )
    client = exchange.bind(EventPrincipal(KEY[1], KEY[0]))
    payload = {"nested": {"value": "original"}, "tenant_id": "spoof", "source": "spoof"}
    assert await client.request(ROOT, payload, correlation_id="turn", causation_id="parent") == "ok"
    assert payload["nested"]["value"] == "original"
    assert observed[0].payload["nested"]["value"] == "original"
    for i, synapse in enumerate(observed):
        assert synapse.source == KEY[1]
        assert synapse.tenant_id == KEY[0]
        assert synapse.correlation_id == "turn"
        assert synapse.causation_id == (observed[i - 1].synapse_id if i else "parent")
    with pytest.raises(EventPermissionError):
        await client.request(LEAF, {})


@pytest.mark.asyncio
async def test_nested_request_without_line_never_reaches_handler():
    registry = SynapticRegistry()
    observed = []

    @registry.subscribe(ROOT, provider="tool.chat")
    async def root(synapse, client):
        return await client.request(LEAF, {})

    @registry.subscribe(LEAF, provider="tool.llm")
    async def leaf(synapse, client):
        observed.append(synapse)

    client = SynapticExchange(registry=registry, authorization=policy()).bind(
        EventPrincipal(KEY[1], KEY[0])
    )
    with pytest.raises(EventPermissionError):
        await client.request(ROOT, {})
    assert observed == []


def test_recursive_delegation_and_excess_depth_deny():
    auth = policy(lines={TrustedLine(ROOT, MIDDLE), TrustedLine(MIDDLE, ROOT)})
    with pytest.raises(EventPermissionError):
        auth.require_invoke(
            context=context(active_signal=MIDDLE, invocation_chain=(ROOT, MIDDLE)), signal=ROOT
        )
    chain = (ROOT, *(f"step.s{i}" for i in range(63)))
    with pytest.raises(EventPermissionError):
        auth.require_invoke(
            context=context(active_signal=chain[-1], invocation_chain=chain), signal=LEAF
        )


@pytest.mark.asyncio
async def test_handler_errors_propagate_and_invalid_payload_is_rejected():
    registry = SynapticRegistry()

    @registry.subscribe(ROOT, provider="tool.chat")
    async def handler(synapse, client):
        raise RuntimeError("provider failed")

    client = SynapticExchange(registry=registry, authorization=policy()).bind(
        EventPrincipal(KEY[1], KEY[0])
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        await client.request(ROOT, {})
    with pytest.raises(TypeError, match="dictionary"):
        await client.request(ROOT, [])
    with pytest.raises(EventPermissionError):
        await client.request(ROOT, {}, target="")
