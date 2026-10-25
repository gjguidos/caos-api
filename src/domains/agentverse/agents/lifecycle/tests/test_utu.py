from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dependency_injector import providers

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    AgentPrincipalResolver,
    UnboundAgentSessionAuthority,
    VerifiedAgentSession,
)
from src.domains.agentverse.agents.lifecycle.anubis import Anubis
from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.agents.lifecycle.models import AgentLifecycleState
from src.domains.agentverse.agents.lifecycle.service import AgentLifecycleService
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey, AgentInstanceRegistryV2
from src.domains.agentverse.runtime.presence import AgentLease
from src.domains.agentverse.security.revocation import policy_version
from src.domains.agentverse.security.utu import UTUAuthority

KEY = AgentInstanceKey("tenant", "owner", "agent", "session")
SESSION = VerifiedAgentSession("tenant", "owner", "session", {"agent"}, active=True)
APPROVAL = AgentActivationApproval("tenant", "agent", "owner", True)
SCOPE = ("tenant", "agent")


def setup(*, approval=APPROVAL, grants=True, session=SESSION):
    sessions = SimpleNamespace(
        get_session=AsyncMock(return_value=session), bind_revocation_handler=Mock()
    )
    definitions = SimpleNamespace(
        find_by_id=AsyncMock(return_value={"id": "agent", "creator": "owner"})
    )
    policy = SynapticAuthorization(
        grants={SCOPE: SynapticGrants({"chat.respond"})} if grants else {},
        trusted_lines=[
            TrustedLine("chat.respond", "llm.generate", tenant_id="tenant", principal_id="agent")
        ],
    )
    verifier = AgentPrincipalResolver(
        definitions=definitions,
        sessions=sessions,
        authorization=policy,
        approvals={SCOPE: approval} if approval else {},
    )
    revocations = SimpleNamespace(
        read=AsyncMock(return_value=(policy_version(policy), "incarnation", "0", "0"))
    )
    utu = UTUAuthority(resolver=verifier, authorization=policy, revocations=revocations)
    runtime = SimpleNamespace(
        id="agent",
        tenant_id="tenant",
        user_id="owner",
        session_id="session",
        connect=AsyncMock(),
        start=AsyncMock(),
        stop=AsyncMock(),
        revoke=Mock(),
    )
    factory = SimpleNamespace(build=AsyncMock(return_value=runtime))
    presence = SimpleNamespace(
        claim=AsyncMock(return_value=AgentLease("worker", "token")),
        is_owner=AsyncMock(return_value=True),
        renew=AsyncMock(return_value=True),
        release=AsyncMock(return_value=True),
    )
    instances = AgentInstanceRegistryV2()
    lifecycle = AgentLifecycleService(
        instances=instances,
        presence=presence,
        factory=factory,
        runtime_id="worker",
        authority=utu,
    )
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["activate", "status", "deactivate"])
@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id", "agent_id"])
async def test_cross_identity_lifecycle_denied_before_state_or_runtime_access(operation, field):
    env = setup()
    with pytest.raises(EventPermissionError):
        await getattr(env.lifecycle, operation)(replace(KEY, **{field: "other"}))
    env.presence.claim.assert_not_awaited()
    env.presence.is_owner.assert_not_awaited()
    env.presence.release.assert_not_awaited()
    env.factory.build.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["activate", "status", "deactivate"])
async def test_missing_approval_denies_all_lifecycle_operations(operation):
    env = setup(approval=None)
    with pytest.raises(EventPermissionError):
        await getattr(env.lifecycle, operation)(KEY)
    env.factory.build.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisors_recheck_owner_before_reuse_status_and_stop():
    env = setup()
    khepri = Khepri(lifecycle=env.lifecycle)
    anubis = Anubis(lifecycle=env.lifecycle)
    try:
        assert (await khepri.awaken(KEY)).state == AgentLifecycleState.ACTIVE
        assert (await khepri.awaken(KEY)).already_active
        assert (await anubis.status(KEY)).state == AgentLifecycleState.ACTIVE
        env.sessions.get_session.return_value = replace(SESSION, user_id="intruder")
        for call in (khepri.awaken, khepri.status, anubis.status, anubis.sleep):
            with pytest.raises(EventPermissionError):
                await call(KEY)
        assert env.instances.get(KEY) is env.runtime
        env.runtime.revoke.assert_not_called()
        env.runtime.stop.assert_not_awaited()
        env.sessions.get_session.return_value = SESSION
        assert (await anubis.sleep(KEY)).state == AgentLifecycleState.SLEEPING
        env.runtime.revoke.assert_called_once()
        env.runtime.stop.assert_awaited_once()
        assert env.instances.get(KEY) is None
        env.factory.build.assert_awaited_once()
    finally:
        await env.lifecycle.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [False, True])
async def test_owner_can_inspect_and_stop_without_agent_grants_or_enablement(disabled):
    env = setup(grants=False, approval=replace(APPROVAL, enabled=not disabled))
    if disabled:
        env.definitions.find_by_id.return_value["disabled"] = True
    with pytest.raises(EventPermissionError):
        await env.utu.resolve_agent_principal(KEY)
    with pytest.raises(EventPermissionError):
        await env.lifecycle.activate(KEY)
    assert (await env.lifecycle.status(KEY)).state == AgentLifecycleState.SLEEPING
    assert (await env.lifecycle.deactivate(KEY)).state == AgentLifecycleState.SLEEPING
    env.factory.build.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["shutdown", "lost_lease"])
async def test_trusted_cleanup_still_stops_after_session_revocation(cleanup):
    env = setup()
    await env.lifecycle.activate(KEY)
    env.sessions.get_session.return_value = None
    with pytest.raises(EventPermissionError):
        await env.lifecycle.deactivate(KEY)
    if cleanup == "shutdown":
        await env.lifecycle.shutdown()
    else:
        await env.lifecycle._cleanup_lost_runtime(KEY)
    env.runtime.revoke.assert_called_once()
    env.runtime.stop.assert_awaited_once()
    assert env.instances.get(KEY) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["activate", "status", "deactivate"])
async def test_missing_lifecycle_authority_denies(operation):
    lifecycle = AgentLifecycleService(
        instances=AgentInstanceRegistryV2(),
        presence=SimpleNamespace(),
        factory=SimpleNamespace(),
        runtime_id="worker",
    )
    with pytest.raises(EventPermissionError, match="not bound"):
        await getattr(lifecycle, operation)(KEY)


@pytest.mark.asyncio
async def test_utu_shared_policy_preserves_nested_lines_and_root_identity():
    env = setup()
    registry = SynapticRegistry()
    observed = []

    @registry.subscribe("chat.respond", provider="chat")
    async def root(synapse, client):
        observed.append(synapse)
        return await client.request("llm.generate", {"tenant_id": "forged"})

    @registry.subscribe("llm.generate", provider="llm")
    async def leaf(synapse, client):
        observed.append(synapse)
        return "ok"

    client = SynapticExchange(registry=registry, authorization=env.utu).bind(
        await env.utu.resolve_agent_principal(KEY)
    )
    assert await client.request("chat.respond", {}) == "ok"
    assert all((s.source, s.tenant_id) == ("agent", "tenant") for s in observed)
    assert observed[1].causation_id == observed[0].synapse_id
    assert observed[1].correlation_id == observed[0].correlation_id
    with pytest.raises(EventPermissionError):
        await client.request("llm.generate", {})
    other = SynapticExchange(registry=registry, authorization=env.utu).bind(
        EventPrincipal("agent", "other")
    )
    with pytest.raises(EventPermissionError):
        await other.request("chat.respond", {})


@pytest.mark.asyncio
async def test_di_shares_utu_and_policy_and_custom_connector_does_not_grant_caller_authority():
    container = AgentverseContainer()
    container.mongodb_repository.override(providers.Object(object()))
    container.redis_repository.override(providers.Object(object()))
    utu = container.utu_authority()
    assert utu is container.utu_authority()
    assert utu._authorization is container.synaptic_authorization()
    assert utu._resolver._authorization is container.synaptic_authorization()
    assert container.synaptic_exchange()._authorization is utu
    assert container.agent_lifecycle_service()._authority is utu
    assert container.khepri()._lifecycle is container.anubis()._lifecycle
    assert container.agent_principal_resolver().__self__ is utu
    assert container.trusted_ncsc_connector()._resolve_principal.__self__ is utu
    # A trusted connector override cannot silently authorize lifecycle callers.
    container.agent_principal_resolver.override(
        providers.Object(AsyncMock(return_value=EventPrincipal("agent", "tenant")))
    )
    for operation in ("activate", "status", "deactivate"):
        with pytest.raises(EventPermissionError, match="not bound"):
            await getattr(container.agent_lifecycle_service(), operation)(KEY)


@pytest.mark.asyncio
async def test_unbound_session_authority_and_unknown_operation_fail_closed():
    env = setup()
    env.verifier._sessions = UnboundAgentSessionAuthority()
    for operation in ("activate", "status", "deactivate", "unknown"):
        with pytest.raises(EventPermissionError):
            await env.utu.authorize_lifecycle(KEY, operation=operation)


@pytest.mark.asyncio
async def test_disabled_running_agent_can_be_stopped_by_verified_owner():
    env = setup()
    try:
        await env.lifecycle.activate(KEY)
        env.definitions.find_by_id.return_value["disabled"] = True
        with pytest.raises(EventPermissionError):
            await env.lifecycle.activate(KEY)
        assert (await env.lifecycle.status(KEY)).state == AgentLifecycleState.ACTIVE
        await env.lifecycle.deactivate(KEY)
        env.runtime.stop.assert_awaited_once()
    finally:
        await env.lifecycle.shutdown()


@pytest.mark.asyncio
async def test_deactivation_rechecks_session_after_waiting_for_instance_lock():
    import asyncio

    env = setup()
    await env.lifecycle.activate(KEY)
    try:
        async with env.instances.lock(KEY):
            stopping = asyncio.create_task(env.lifecycle.deactivate(KEY))
            await asyncio.sleep(0)
            env.sessions.get_session.return_value = None
        with pytest.raises(EventPermissionError):
            await stopping
        env.runtime.stop.assert_not_awaited()
    finally:
        await env.lifecycle.shutdown()


@pytest.mark.asyncio
async def test_owner_can_stop_running_agent_after_policy_snapshot_loses_grants():
    env = setup()
    try:
        await env.lifecycle.activate(KEY)
        # Model coordinated replacement of the lifecycle authority snapshot.
        # Existing clients require separate retirement, as documented.
        env.lifecycle._authority = setup(grants=False).utu
        with pytest.raises(EventPermissionError):
            await env.lifecycle.activate(KEY)
        assert (await env.lifecycle.status(KEY)).state == AgentLifecycleState.ACTIVE
        await env.lifecycle.deactivate(KEY)
        env.runtime.stop.assert_awaited_once()
        assert env.instances.get(KEY) is None
    finally:
        await env.lifecycle.shutdown()
