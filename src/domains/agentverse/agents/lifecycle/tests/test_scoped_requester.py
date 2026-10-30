"""Signed requester claims, real isolated Redis sessions, and current UTU fences."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.tests import test_session_authority as session_tests
from src.domains.agentverse.agents.lifecycle.tests.test_utu import KEY
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal

CHAT = CapabilityBinding("chat.respond", "chat", "1.0.0")
SCOPE = CapabilityScope("resource.context", CHAT, frozenset({CHAT}))
session_env = session_tests.session_env
store = session_tests.store


async def provision(s, role):
    sid = "requester-" + uuid4().hex
    token = s.sign(sid=sid, roles=[role], scp="agent.read")
    await s.sessions.provision(
        token,
        tenant_id=KEY.tenant_id,
        user_id=KEY.user_id,
        allowed_agent_ids=frozenset({KEY.agent_id}),
    )
    return token, replace(KEY, session_id=sid)


@pytest.mark.asyncio
async def test_signed_roles_bound_to_subject_and_ticket_and_not_payload(session_env):
    s = session_env
    token, key = await provision(s, "team-a.user")
    async with s.sessions.authenticated(token):
        principal = await s.env.utu.attenuate(await s.env.utu.resolve_agent_principal(key), SCOPE)
        registry = SynapticRegistry()

        @registry.subscribe(CHAT.signal, provider=CHAT.provider)
        async def handler(_synapse, _client):
            return "read-only"

        client = SynapticExchange(registry=registry, authorization=s.env.utu).bind(principal)
        assert await client.request(CHAT.signal, {}) == "read-only"
        user = await s.env.utu.resource_requester(principal.permit)
        assert (user.id, user.tenant_id, user.roles, user.scopes, user.access_token) == (
            key.user_id,
            key.tenant_id,
            ["team-a.user"],
            ["agent.read"],
            None,
        )
        user.roles.append("forged.admin")
        assert (await s.env.utu.resource_requester(principal.permit)).roles == ["team-a.user"]
        ticket = await s.sessions.issue_ws_ticket()
    with pytest.raises(EventPermissionError):
        await s.env.utu.resource_requester(principal.permit)
    async with s.sessions.authenticated_ticket(ticket):
        assert (await s.env.utu.resource_requester(principal.permit)).roles == ["team-a.user"]
    # Changed claims under the same session ID cannot replace its token binding.
    with pytest.raises(EventPermissionError):
        async with s.sessions.authenticated(s.sign(sid=key.session_id, roles=["other.admin"])):
            pass


@pytest.mark.asyncio
async def test_concurrent_and_nested_contexts_never_cross_requesters(session_env):
    s = session_env
    a, b = await provision(s, "team-a.user"), await provision(s, "team-b.user")

    async def read(pair):
        token, key = pair
        async with s.sessions.authenticated(token):
            permit = (await s.env.utu.resolve_agent_principal(key)).permit
            await asyncio.sleep(0)
            return (await s.env.utu.resource_requester(permit)).roles

    assert await asyncio.gather(read(a), read(b)) == [["team-a.user"], ["team-b.user"]]
    async with s.sessions.authenticated(a[0]):
        permit = (await s.env.utu.resolve_agent_principal(a[1])).permit
        async with s.sessions.authenticated(b[0]):
            with pytest.raises(EventPermissionError):
                await s.env.utu.resource_requester(permit)
        assert (await s.env.utu.resource_requester(permit)).roles == ["team-a.user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expiry", "membership", "revocation"])
async def test_scoped_requester_keeps_existing_freshness_fences(session_env, change):
    s = session_env
    token, key = await provision(s, "team.user")
    registry = SynapticRegistry()

    @registry.subscribe(CHAT.signal, provider=CHAT.provider)
    async def handler(_synapse, _client):
        raise AssertionError("Stale scoped authority must not execute")

    async with s.sessions.authenticated(token):
        principal = await s.env.utu.attenuate(await s.env.utu.resolve_agent_principal(key), SCOPE)
        client = SynapticExchange(registry=registry, authorization=s.env.utu).bind(principal)
        if change == "expiry":
            s.now[0] += 3601
        elif change == "membership":
            await s.sessions.remove_membership(key.tenant_id, key.user_id)
        else:
            await s.env.utu.revoke(key.tenant_id, session_id=key.session_id)
        with pytest.raises(EventPermissionError):
            await s.env.utu.resource_requester(principal.permit)
        with pytest.raises(EventPermissionError):
            await client.request(CHAT.signal, {})


@pytest.mark.asyncio
async def test_malformed_signed_memberships_are_not_adopted(session_env):
    for roles in ("team.admin", {"team": "admin"}, [1], [""]):
        with pytest.raises(EventPermissionError):
            async with session_env.sessions.authenticated(session_env.sign(roles=roles)):
                pass


LLM = CapabilityBinding("llm.generate", "llm", "1.0.0")
NESTED = CapabilityScope("resource.context", CHAT, frozenset({CHAT, LLM}))
STORE_SIGNAL = "store.create.provisioned"


async def invalidate(s, key, change):
    if change == "expiry":
        s.now[0] += 3601
    elif change == "revocation":
        await s.env.utu.revoke(key.tenant_id, session_id=key.session_id)
    else:
        # Any policy transition supersedes every permit issued under the old one.
        expected = (await s.store.read(key))[0]
        await s.env.utu.install_policy(key.tenant_id, expected=expected, authorization=s.env.policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expiry", "revocation", "policy"])
async def test_stale_permits_cannot_be_attenuated(session_env, change):
    s = session_env
    token, key = await provision(s, "team.user")
    async with s.sessions.authenticated(token):
        principal = await s.env.utu.resolve_agent_principal(key)
        scoped = await s.env.utu.attenuate(principal, SCOPE)
        await invalidate(s, key, change)
        for candidate in (principal, scoped):
            with pytest.raises(EventPermissionError):
                await s.env.utu.attenuate(candidate, SCOPE)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expiry", "revocation", "policy"])
async def test_rebound_child_honors_permit_freshness_and_never_reaches_store(session_env, change):
    s = session_env
    token, key = await provision(s, "team.user")
    # attenuate() conservatively requires every scoped signal to be a root grant.
    broader = SynapticAuthorization(
        grants={(key.tenant_id, key.agent_id): SynapticGrants({CHAT.signal, LLM.signal})},
        trusted_lines=s.env.policy._trusted_lines,
    )
    await s.env.utu.install_policy(
        key.tenant_id, expected=(await s.store.read(key))[0], authorization=broader
    )
    registry = SynapticRegistry()
    children = []

    @registry.subscribe(CHAT.signal, provider=CHAT.provider)
    async def root(_synapse, client):
        children.append(client)
        return "root"

    @registry.subscribe(LLM.signal, provider=LLM.provider)
    async def nested(_synapse, _client):
        return "nested"

    @registry.subscribe(STORE_SIGNAL, provider="system.store")
    async def store(_synapse, _client):
        raise AssertionError("Validation-only authority must never reach Store")

    exchange = SynapticExchange(registry=registry, authorization=s.env.utu)
    async with s.sessions.authenticated(token):
        principal = await s.env.utu.attenuate(await s.env.utu.resolve_agent_principal(key), NESTED)
        assert await exchange.bind(principal).request(CHAT.signal, {}) == "root"
        permit = children[0].execution_permit
        # The original handle is gone; trusted code rebinds the child permit.
        children[0].revoke()
        rebound = exchange.bind(AuthorizedAgentPrincipal(key.agent_id, key.tenant_id, permit))
        assert await rebound.request(LLM.signal, {}) == "nested"
        with pytest.raises(EventPermissionError):
            await rebound.request(STORE_SIGNAL, {})
        await invalidate(s, key, change)
        for signal in (LLM.signal, STORE_SIGNAL):
            with pytest.raises(EventPermissionError):
                await rebound.request(signal, {})
