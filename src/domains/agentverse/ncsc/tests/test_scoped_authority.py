"""Attenuation uses existing grants AND trusted lines; it never replaces either."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    AgentPrincipalResolver,
    SystemCitizen,
    VerifiedAgentSession,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority

KEY = AgentInstanceKey("tenant", "owner", "genesis", "session")
VALIDATE = CapabilityBinding("data.validation", "system.validation", "1.0.0")
STORE = CapabilityBinding("store.create.provisioned", "system.store", "1.0.0")
SCOPE = CapabilityScope("agent.creation_draft.validate", VALIDATE, frozenset({VALIDATE}))


def environment(*, lines=(), grants=None):
    policy = SynapticAuthorization(
        grants={
            (KEY.tenant_id, KEY.agent_id): SynapticGrants(
                frozenset({VALIDATE.signal, STORE.signal} if grants is None else grants)
            )
        },
        trusted_lines=lines,
    )
    versions = (policy_version(policy), "incarnation", "0", "0")
    session = VerifiedAgentSession(
        KEY.tenant_id, KEY.user_id, KEY.session_id, frozenset({KEY.agent_id}), True
    )
    sessions = SimpleNamespace(
        get_session=AsyncMock(return_value=session),
        bind_revocation_handler=Mock(),
        resource_requester=AsyncMock(
            return_value=User(id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["team.user"])
        ),
    )
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock()),
        sessions=sessions,
        authorization=policy,
        approvals={
            (KEY.tenant_id, KEY.agent_id): AgentActivationApproval(
                KEY.tenant_id, KEY.agent_id, KEY.user_id, True
            )
        },
        citizens={
            (KEY.tenant_id, KEY.agent_id): SystemCitizen(KEY.tenant_id, KEY.agent_id, "Genesis")
        },
    )
    revocations = SimpleNamespace(read=AsyncMock(return_value=versions))
    authority = UTUAuthority(resolver=resolver, authorization=policy, revocations=revocations)
    registry = SynapticRegistry()
    calls, children = [], []

    async def validation(synapse, client):
        calls.append(synapse)
        children.append(client)
        return {"valid": True}

    async def store(synapse, client):
        raise AssertionError("Store must not execute through a scoped validation client")

    registry.register(
        VALIDATE.signal, component=SynapticBinding(VALIDATE.signal, VALIDATE.provider, validation)
    )
    registry.register(STORE.signal, component=SynapticBinding(STORE.signal, STORE.provider, store))
    exchange = SynapticExchange(registry=registry, authorization=authority)
    return SimpleNamespace(**locals())


async def scoped(env, scope=SCOPE):
    principal = await env.authority.resolve_agent_principal(KEY)
    principal = await env.authority.attenuate(principal, scope)
    return env.exchange.bind(principal), principal


@pytest.mark.asyncio
async def test_exact_binding_and_evidence_and_root_grants_remain_unchanged():
    env = environment()
    client, principal = await scoped(env)
    assert await client.request(VALIDATE.signal, {}, version="1.0.0") == {"valid": True}
    assert env.calls[0].provider_version == "1.0.0"
    assert env.calls[0].entry_operation == SCOPE.operation
    assert env.authority.grants_for(principal).invoke == {VALIDATE.signal, STORE.signal}
    assert principal.permit.scope == SCOPE
    with pytest.raises(FrozenInstanceError):
        principal.permit.scope = None
    with pytest.raises(FrozenInstanceError):
        SCOPE.operation = "other"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signal,version,target",
    [
        (STORE.signal, "1.0.0", None),
        (VALIDATE.signal, "2.0.0", None),
        (VALIDATE.signal, "1.0.0", "other"),
        ("arbitrary.signal", "1.0.0", None),
    ],
)
async def test_gateway_cannot_reach_other_bindings(signal, version, target):
    env = environment()
    client, _ = await scoped(env)
    with pytest.raises(EventPermissionError):
        await client.request(signal, {}, version=version, target=target)
    assert env.calls == []


@pytest.mark.asyncio
async def test_scope_must_be_a_subset_of_principal_and_parent_authority():
    env = environment(grants={VALIDATE.signal})
    principal = await env.authority.resolve_agent_principal(KEY)
    broad = CapabilityScope(SCOPE.operation, VALIDATE, frozenset({VALIDATE, STORE}))
    with pytest.raises(EventPermissionError):
        await env.authority.attenuate(principal, broad)
    env = environment()
    _, principal = await scoped(env)
    for wider in (broad, replace(SCOPE, operation="other.operation")):
        with pytest.raises(EventPermissionError):
            await env.authority.attenuate(principal, wider)


@pytest.mark.asyncio
async def test_scope_and_lineage_survive_rebind_retry_child_and_trusted_line():
    env = environment(lines=(TrustedLine(VALIDATE.signal, STORE.signal),))
    client, principal = await scoped(env)
    for rebound in (client, env.exchange.bind(principal)):
        assert await rebound.request(VALIDATE.signal, {}) == {"valid": True}
    child = env.children[0]
    for nested in (
        child,
        env.exchange.bind(
            AuthorizedAgentPrincipal(KEY.agent_id, KEY.tenant_id, child.execution_permit)
        ),
    ):
        with pytest.raises(EventPermissionError):
            await nested.request(STORE.signal, {})
        with pytest.raises(EventPermissionError):
            await nested.request(VALIDATE.signal, {})  # no rebinding into a fresh root


@pytest.mark.asyncio
async def test_trusted_lines_are_independent_even_when_scope_allows_both_signals():
    env = environment()
    broad = CapabilityScope(SCOPE.operation, VALIDATE, frozenset({VALIDATE, STORE}))
    client, _ = await scoped(env, broad)
    await client.request(VALIDATE.signal, {})
    with pytest.raises(EventPermissionError):
        await env.children[0].request(STORE.signal, {})


@pytest.mark.asyncio
async def test_missing_version_or_replaced_provider_fails_closed():
    env = environment()
    client, _ = await scoped(env)
    env.registry.unregister(VALIDATE.signal)
    with pytest.raises(KeyError):
        await client.request(VALIDATE.signal, {})
    env.registry.register(
        VALIDATE.signal,
        version="2.0.0",
        component=SynapticBinding(VALIDATE.signal, VALIDATE.provider, env.validation, "2.0.0"),
    )
    with pytest.raises((KeyError, ValueError)):
        await client.request(VALIDATE.signal, {})
    env.registry.unregister(VALIDATE.signal)
    env.registry.register(
        VALIDATE.signal, component=SynapticBinding(VALIDATE.signal, "replacement", env.validation)
    )
    with pytest.raises(EventPermissionError):
        await client.request(VALIDATE.signal, {})
    assert not env.calls


@pytest.mark.asyncio
async def test_revocation_supersession_and_missing_requester_remain_fail_closed():
    env = environment()
    client, principal = await scoped(env)
    env.revocations.read.return_value = ("changed", "incarnation", "0", "0")
    with pytest.raises(EventPermissionError):
        await client.request(VALIDATE.signal, {})
    env = environment()
    _, principal = await scoped(env)
    env.sessions.resource_requester.return_value = User(id="other", tenant_id=KEY.tenant_id)
    with pytest.raises(EventPermissionError):
        await env.authority.resource_requester(principal.permit)
    del env.sessions.resource_requester
    with pytest.raises(EventPermissionError):
        await env.authority.resource_requester(principal.permit)


def test_unknown_mutable_or_malformed_restrictions_are_rejected():
    for value in ({}, [], "validation"):
        with pytest.raises(ValueError):
            ExecutionPermit(KEY, ("v",), scope=value)
    with pytest.raises(ValueError):
        CapabilityScope(SCOPE.operation, VALIDATE, [VALIDATE])
    for version in ("latest", "*", ">=1.0.0", ""):
        with pytest.raises(ValueError):
            CapabilityBinding(VALIDATE.signal, VALIDATE.provider, version)
    with pytest.raises(ValueError):
        ExecutionPermit(KEY, ("v",), binding_chain=(VALIDATE,))


@pytest.mark.asyncio
async def test_in_place_provider_upgrade_cannot_redirect_a_pinned_scope():
    env = environment()
    client, _ = await scoped(env)
    # An unfrozen registry accepts the same provider/handler at a new version.
    env.registry.register(
        VALIDATE.signal,
        version="1.1.0",
        component=SynapticBinding(VALIDATE.signal, VALIDATE.provider, env.validation, "1.1.0"),
    )
    for version in (None, "1.0.0"):
        with pytest.raises(KeyError):
            await client.request(VALIDATE.signal, {}, version=version)
    with pytest.raises(EventPermissionError):
        await client.request(VALIDATE.signal, {}, version="1.1.0")
    assert not env.calls


@pytest.mark.asyncio
async def test_unscoped_internal_requests_stay_compatible_and_may_pin():
    env = environment()
    client = env.exchange.bind(await env.authority.resolve_agent_principal(KEY))
    assert await client.request(VALIDATE.signal, {}) == {"valid": True}
    assert await client.request(VALIDATE.signal, {}, version="1.0.0") == {"valid": True}
    assert [synapse.entry_operation for synapse in env.calls] == [None, None]
    assert {synapse.provider_version for synapse in env.calls} == {"1.0.0"}
    with pytest.raises(KeyError):
        await client.request(VALIDATE.signal, {}, version="2.0.0")
    assert len(env.calls) == 2
