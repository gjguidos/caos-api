"""Real Redis 6 integration. Run with UTU_TEST_REDIS_URL; never flush shared DBs."""

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.domains.agentverse.agents.lifecycle.tests.test_utu import KEY, setup
from src.domains.agentverse.agents.runtime_v2 import ManagedAgentV2
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.security.revocation import RedisRevocationStore
from src.domains.agentverse.security.utu import UTUAuthority


@pytest_asyncio.fixture
async def store():
    url = os.environ.get("UTU_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set UTU_TEST_REDIS_URL for Redis 6 integration tests")
    redis = Redis.from_url(url)
    prefix = "test:utu:" + uuid4().hex + ":"
    keys = set()

    class IsolatedStore(RedisRevocationStore):
        @staticmethod
        def _key(tenant):
            key = prefix + RedisRevocationStore._key(tenant)
            keys.add(key)
            return key

    result = IsolatedStore(repository=RedisRepositoryImpl(redis), timeout=1.0)
    try:
        yield result
    finally:
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def environment(store):
    env = setup()
    env.utu._revocations = store
    env.utu.poll_interval = 0.01
    await env.utu.install_policy("tenant", expected=None, authorization=env.policy)
    return env


async def client_for(env, handler=None):
    registry = SynapticRegistry()
    children = []

    @registry.subscribe("chat.respond", provider="chat")
    async def root(synapse, client):
        children.append(client)
        return await handler(client) if handler else "ok"

    @registry.subscribe("llm.generate", provider="llm")
    async def leaf(synapse, client):
        return "leaf"

    exchange = SynapticExchange(registry=registry, authorization=env.utu)
    client = exchange.bind(await env.utu.resolve_agent_principal(KEY))
    return client, children, exchange


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [{}, {"agent_id": "agent"}, {"session_id": "session"}])
async def test_existing_root_and_saved_nested_client_revoked(store, scope):
    env = await environment(store)
    client, children, exchange = await client_for(env)
    assert await client.request("chat.respond", {}) == "ok"
    assert await children[0].request("llm.generate", {}) == "leaf"
    assert await env.utu.revoke("tenant", **scope)
    assert not await env.utu.revoke("tenant", **scope)
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})
    with pytest.raises(EventPermissionError):
        await children[0].request("llm.generate", {})
    with pytest.raises(EventPermissionError):
        await env.utu.resolve_agent_principal(KEY)
    with pytest.raises(EventPermissionError):
        await exchange.bind(EventPrincipal("agent", "tenant")).request("chat.respond", {})


@pytest.mark.asyncio
async def test_concurrent_workers_and_tenant_isolation(store):
    env = await environment(store)
    remote = setup()
    remote.utu._revocations = store
    client, _, _ = await client_for(remote)
    await store.install_policy("other", expected=None, version=env.utu._version)
    other_key = replace(KEY, tenant_id="other")
    before = await store.read(other_key)
    results = await asyncio.gather(*[store.revoke("tenant", agent_id="agent") for _ in range(10)])
    assert results.count(True) == 1
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})
    assert await store.read(other_key) == before


@pytest.mark.asyncio
async def test_policy_cas_stale_workers_and_aba(store):
    env = await environment(store)
    old_client, _, _ = await client_for(env)
    first = (await store.read(KEY))[0]
    deny = SynapticAuthorization()
    second = await env.utu.install_policy("tenant", expected=first, authorization=deny)
    with pytest.raises(EventPermissionError):
        await old_client.request("chat.respond", {})
    with pytest.raises(EventPermissionError):
        await env.utu.install_policy("tenant", expected=first, authorization=env.policy)
    remote = setup()
    remote.utu._revocations = store
    with pytest.raises(EventPermissionError):
        await remote.utu.resolve_agent_principal(KEY)
    third = await env.utu.install_policy("tenant", expected=second, authorization=env.policy)
    assert third != first
    with pytest.raises(EventPermissionError):
        await old_client.request("chat.respond", {})
    new_client, _, _ = await client_for(env)
    assert await new_client.request("chat.respond", {}) == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["build", "connect", "start"])
async def test_revocation_during_activation(store, phase):
    env = await environment(store)
    reached = asyncio.Event()

    async def wait():
        reached.set()
        await asyncio.Event().wait()

    if phase == "build":
        env.factory.build.side_effect = wait

        # build accepts the instance key
        async def build(key):
            await wait()

        env.factory.build.side_effect = build
    else:
        getattr(env.runtime, phase).side_effect = wait
    activation = asyncio.create_task(env.lifecycle.activate(KEY))
    await asyncio.wait_for(reached.wait(), 1)
    await env.utu.revoke("tenant", session_id="session")
    with pytest.raises(asyncio.CancelledError):
        await activation
    await env.lifecycle.shutdown()
    assert not env.instances.exists(KEY)
    env.presence.release.assert_awaited()
    with pytest.raises(EventPermissionError):
        await env.lifecycle.activate(KEY)


@pytest.mark.asyncio
async def test_remote_revocation_cancels_inflight_and_stops_runtime(store):
    env = await environment(store)
    reached = asyncio.Event()

    async def effect(client):
        reached.set()
        await asyncio.Event().wait()

    client, _, _ = await client_for(env, effect)
    task = asyncio.create_task(client.request("chat.respond", {}))
    await reached.wait()
    await env.lifecycle.activate(KEY)
    # Another worker writes Redis directly; no local notification is delivered.
    await store.revoke("tenant", session_id="session")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    async with asyncio.timeout(1):
        while KEY in env.lifecycle._active:
            await asyncio.sleep(0.01)
    env.runtime.revoke.assert_called()
    env.runtime.stop.assert_awaited_once()
    env.presence.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_loss_and_timeout_fail_closed(store):
    env = await environment(store)
    client, _, _ = await client_for(env)
    original = store._repo
    store._repo = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError()))
    with pytest.raises(EventPermissionError, match="unavailable"):
        await client.request("chat.respond", {})
    with pytest.raises(EventPermissionError):
        await env.lifecycle.activate(KEY)

    async def hung(*args, **kwargs):
        await asyncio.Event().wait()

    store._timeout = 0.05
    store._repo = SimpleNamespace(eval=hung)
    with pytest.raises(EventPermissionError):
        await asyncio.wait_for(client.request("chat.respond", {}), 1)
    store._repo = original


@pytest.mark.asyncio
async def test_failed_stop_keeps_renewing_ownership(store):
    env = await environment(store)
    await env.lifecycle.activate(KEY)
    env.runtime.stop.side_effect = RuntimeError("not quiescent")
    await env.utu.revoke("tenant", agent_id="agent")
    await asyncio.sleep(0.05)
    assert KEY in env.lifecycle._active
    assert env.instances.get(KEY) is env.runtime
    env.presence.release.assert_not_awaited()
    env.presence.renew.assert_awaited()
    env.runtime.stop.side_effect = None
    await env.lifecycle.shutdown()


@pytest.mark.asyncio
async def test_runtime_revoke_fences_saved_client_and_drains(store):
    env = await environment(store)
    client, _, _ = await client_for(env)
    runtime = ManagedAgentV2(key=KEY, ncsc_connector=AsyncMock(return_value=client))
    await runtime.connect()
    await runtime.start()
    saved = runtime.ncsc
    runtime.revoke()
    with pytest.raises(EventPermissionError):
        await saved.request("chat.respond", {})
    await runtime.stop()


@pytest.mark.asyncio
async def test_missing_session_invalidation_adapter_denies(store):
    env = await environment(store)
    env.verifier._sessions = SimpleNamespace(get_session=AsyncMock())
    authority = UTUAuthority(resolver=env.verifier, authorization=env.policy, revocations=store)
    with pytest.raises(EventPermissionError, match="adapter"):
        await authority.resolve_agent_principal(KEY)


@pytest.mark.asyncio
async def test_unprovisioned_and_deleted_records_deny_old_clients(store):
    env = setup()
    env.utu._revocations = store
    with pytest.raises(EventPermissionError):
        await env.utu.resolve_agent_principal(KEY)
    await env.utu.install_policy("tenant", expected=None, authorization=env.policy)
    client, _, _ = await client_for(env)
    await store._repo.delete(store._key("tenant"))
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})
    await env.utu.install_policy("tenant", expected=None, authorization=env.policy)
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})


@pytest.mark.asyncio
async def test_local_revoke_cancels_runtime_work_before_lease_release(store):
    env = await environment(store)
    reached = asyncio.Event()
    cancelled = asyncio.Event()

    async def effect(client):
        reached.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client, _, _ = await client_for(env, effect)
    runtime = ManagedAgentV2(key=KEY, ncsc_connector=AsyncMock(return_value=client))
    env.factory.build.return_value = runtime
    await env.lifecycle.activate(KEY)
    task = asyncio.create_task(runtime.ncsc.request("chat.respond", {}))
    await reached.wait()
    await env.utu.revoke("tenant", agent_id="agent")
    assert not runtime.is_active
    with pytest.raises(asyncio.CancelledError):
        await task
    await env.lifecycle.shutdown()
    assert cancelled.is_set()
    env.presence.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_construction_keeps_lease_while_cancellation_is_draining(store):
    env = await environment(store)
    reached = asyncio.Event()
    finishing = asyncio.Event()

    async def build(key):
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await finishing.wait()
            raise

    env.factory.build.side_effect = build
    activation = asyncio.create_task(env.lifecycle.activate(KEY))
    await reached.wait()
    # Remote revocation: the guard, rather than a notification, must interrupt build.
    await store.revoke("tenant", session_id="session")
    await asyncio.sleep(0.06)
    assert not activation.done()
    env.presence.renew.assert_awaited()
    env.presence.release.assert_not_awaited()
    finishing.set()
    with pytest.raises(asyncio.CancelledError):
        await activation
    env.presence.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_adapter_registers_durable_invalidation_handler(store):
    env = await environment(store)
    handler = env.sessions.bind_revocation_handler.call_args.args[0]
    client, _, _ = await client_for(env)
    await handler("tenant", session_id="session")
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})


@pytest.mark.asyncio
async def test_redis_loss_stops_idle_runtime(store):
    env = await environment(store)
    await env.lifecycle.activate(KEY)
    original = store._repo
    store._repo = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError()))
    async with asyncio.timeout(1):
        while KEY in env.lifecycle._active:
            await asyncio.sleep(0.01)
    store._repo = original
    env.runtime.revoke.assert_called()
    env.runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_di_keeps_live_authority_identity_across_reload(store):
    from dependency_injector import providers

    from src.domains.agentverse.dependencies.di_container import AgentverseContainer

    env = setup()
    container = AgentverseContainer()
    container.mongodb_repository.override(providers.Object(object()))
    container.redis_repository.override(providers.Object(store._repo))
    container.genesis_sessions.override(providers.Object(env.sessions))
    container.synaptic_authorization.override(providers.Object(env.policy))
    utu = container.utu_authority()
    utu._revocations = store
    exchange = container.synaptic_exchange()
    lifecycle = container.agent_lifecycle_service()
    first = await utu.install_policy("tenant", expected=None, authorization=env.policy)
    changed = SynapticAuthorization()
    await utu.install_policy("tenant", expected=first, authorization=changed)
    assert container.utu_authority() is utu
    assert exchange._authorization is utu
    assert lifecycle._authority is utu
    assert container.agent_identity_verifier()._authorization is changed
    assert container.khepri()._lifecycle is container.anubis()._lifecycle
    remote = setup()
    remote.utu._revocations = store
    remote.utu.reload_policy(changed)
    assert remote.verifier._authorization is changed
    with pytest.raises(EventPermissionError):
        await remote.utu.resolve_agent_principal(KEY)


@pytest.mark.asyncio
async def test_cancellation_resistant_effect_retains_ownership_until_drained(store):
    env = await environment(store)
    reached = asyncio.Event()
    cancelled = asyncio.Event()
    finishing = asyncio.Event()

    async def effect(client):
        reached.set()
        while not finishing.is_set():
            try:
                await finishing.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return "ignored cancellation"

    client, _, _ = await client_for(env, effect)
    runtime = ManagedAgentV2(key=KEY, ncsc_connector=AsyncMock(return_value=client))
    env.factory.build.return_value = runtime
    await env.lifecycle.activate(KEY)
    task = asyncio.create_task(runtime.ncsc.request("chat.respond", {}))
    await reached.wait()
    await env.utu.revoke("tenant", agent_id="agent")
    await cancelled.wait()
    # stop's finite drain expires; the supervisor must retain the runtime/lease.
    await asyncio.sleep(2.1)
    assert KEY in env.lifecycle._active
    assert env.instances.get(KEY) is runtime
    env.presence.release.assert_not_awaited()
    env.presence.renew.assert_awaited()
    finishing.set()
    with pytest.raises(EventPermissionError):
        await task
    await env.lifecycle.shutdown()
    env.presence.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_nested_inflight_cancellation_drains_all_tracking(store):
    env = await environment(store)
    reached = asyncio.Event()
    registry = SynapticRegistry()

    @registry.subscribe("chat.respond", provider="chat")
    async def root(synapse, client):
        return await client.request("llm.generate", {})

    @registry.subscribe("llm.generate", provider="llm")
    async def leaf(synapse, client):
        reached.set()
        await asyncio.Event().wait()

    exchange = SynapticExchange(registry=registry, authorization=env.utu)
    client = exchange.bind(await env.utu.resolve_agent_principal(KEY))
    task = asyncio.create_task(client.request("chat.respond", {}))
    await reached.wait()
    await store.revoke("tenant", session_id="session")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert not env.utu._work
    assert not client._context.gate.tasks


@pytest.mark.asyncio
async def test_other_tenant_client_continues_after_agent_revocation(store):
    from src.domains.agentverse.agents.lifecycle.tests.test_utu import APPROVAL, SESSION
    from src.domains.agentverse.ncsc.models import SynapticGrants

    env = await environment(store)
    other = setup()
    other.utu._revocations = store
    other_key = replace(KEY, tenant_id="other")
    other.sessions.get_session.return_value = replace(SESSION, tenant_id="other")
    other.verifier._approvals = {("other", "agent"): replace(APPROVAL, tenant_id="other")}
    policy = SynapticAuthorization(grants={("other", "agent"): SynapticGrants({"chat.respond"})})
    await other.utu.install_policy("other", expected=None, authorization=policy)
    registry = SynapticRegistry()

    @registry.subscribe("chat.respond", provider="chat")
    async def root(synapse, client):
        return synapse.tenant_id

    client = SynapticExchange(registry=registry, authorization=other.utu).bind(
        await other.utu.resolve_agent_principal(other_key)
    )
    assert await client.request("chat.respond", {}) == "other"
    await env.utu.revoke("tenant", agent_id="agent")
    assert await client.request("chat.respond", {}) == "other"
