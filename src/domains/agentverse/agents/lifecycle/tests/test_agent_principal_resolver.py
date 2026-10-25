from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventGrants, EventPrincipal
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    AgentPrincipalResolver,
    UnboundAgentSessionAuthority,
    VerifiedAgentSession,
)
from src.domains.agentverse.agents.lifecycle.ncsc_connector import TrustedNCSCConnector
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

KEY = AgentInstanceKey("tenant", "owner", "agent", "session")
SCOPE = (KEY.tenant_id, KEY.agent_id)
APPROVAL = AgentActivationApproval(*SCOPE, owner_user_id="owner", enabled=True)
SESSION = VerifiedAgentSession("tenant", "owner", "session", {"agent"}, active=True)
ROOT = "chat.respond"
LEAF = "llm.generate"


def setup_resolver(*, session=SESSION, approval=APPROVAL, definition=None, grants=None):
    sessions = SimpleNamespace(get_session=AsyncMock(return_value=session))
    definitions = SimpleNamespace(
        find_by_id=AsyncMock(
            return_value=({"id": "agent", "creator": "owner"} if definition is None else definition)
        )
    )
    authorization = SynapticAuthorization(
        grants={SCOPE: SynapticGrants({ROOT})} if grants is None else grants,
        trusted_lines=[TrustedLine(ROOT, LEAF, tenant_id="tenant", principal_id="agent")],
    )
    resolver = AgentPrincipalResolver(
        sessions=sessions,
        definitions=definitions,
        authorization=authorization,
        approvals={} if approval is None else {SCOPE: approval},
    )
    return resolver, sessions, definitions, authorization


@pytest.mark.asyncio
async def test_resolves_persisted_agent_not_lifecycle_caller():
    resolver, sessions, definitions, _ = setup_resolver()
    assert await resolver(KEY) == EventPrincipal("agent", "tenant")
    sessions.get_session.assert_awaited_once_with("session")
    definitions.find_by_id.assert_awaited_once_with("agent")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    [
        None,
        replace(SESSION, active=False),
        replace(SESSION, tenant_id="other"),
        replace(SESSION, user_id="other"),
        replace(SESSION, session_id="other"),
        replace(SESSION, allowed_agent_ids=frozenset()),
        {"user_id": "owner"},
    ],
)
async def test_missing_inactive_mismatched_or_unauthorized_session_denies_before_storage(session):
    resolver, _, definitions, _ = setup_resolver(session=session)
    with pytest.raises(EventPermissionError):
        await resolver(KEY)
    definitions.find_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "user_id", "agent_id", "session_id"])
async def test_forged_instance_key_denies(field):
    resolver, _, _, _ = setup_resolver()
    with pytest.raises(EventPermissionError):
        await resolver(replace(KEY, **{field: "forged"}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "approval", [None, replace(APPROVAL, enabled=False), replace(APPROVAL, owner_user_id="other")]
)
async def test_missing_disabled_or_cross_user_approval_denies(approval):
    resolver, _, _, _ = setup_resolver(approval=approval)
    with pytest.raises(EventPermissionError):
        await resolver(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "definition",
    [
        {},
        {"id": "wrong", "creator": "owner"},
        {"id": "agent", "creator": "other"},
        {"id": "agent"},
        {"id": "agent", "creator": "owner", "tenant_id": "other"},
        {"id": "agent", "creator": "owner", "enabled": False},
        {"id": "agent", "creator": "owner", "disabled": True},
        {
            "id": "agent",
            "creator": "other",
            "visibility": "public",
            "whitelisted_groups": ["admin"],
        },
    ],
)
async def test_definition_identity_owner_tenant_and_disable_checks(definition):
    resolver, _, _, _ = setup_resolver(definition=definition)
    with pytest.raises(EventPermissionError):
        await resolver(KEY)


@pytest.mark.asyncio
async def test_deleted_definition_and_storage_failure_never_bind():
    resolver, _, definitions, _ = setup_resolver()
    exchange = SimpleNamespace(bind=lambda _: pytest.fail("denied request bound"))
    connector = TrustedNCSCConnector(exchange=exchange, resolve_principal=resolver)
    definitions.find_by_id.return_value = None
    with pytest.raises(EventPermissionError):
        await connector(KEY)
    definitions.find_by_id.side_effect = RuntimeError("store unavailable")
    with pytest.raises(RuntimeError, match="store unavailable"):
        await connector(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grants",
    [
        {},
        {SCOPE: SynapticGrants()},
        {("other", "agent"): SynapticGrants({ROOT})},
        {("tenant", "khepri"): SynapticGrants({ROOT})},
    ],
)
async def test_missing_empty_cross_tenant_or_lifecycle_caller_grants_deny(grants):
    resolver, _, _, _ = setup_resolver(grants=grants)
    with pytest.raises(EventPermissionError):
        await resolver(KEY)


@pytest.mark.asyncio
async def test_payload_and_definition_grants_cannot_elevate_real_exchange():
    resolver, _, definitions, policy = setup_resolver()
    definitions.find_by_id.return_value.update(
        grants={"invoke": [LEAF]}, tools=[LEAF], roles=["admin"]
    )
    registry = SynapticRegistry()
    observed = []

    @registry.subscribe(ROOT, provider="chat")
    async def root(synapse, client):
        observed.append(synapse)
        return await client.request(LEAF, {})

    @registry.subscribe(LEAF, provider="llm")
    async def leaf(synapse, client):
        observed.append(synapse)
        return "ok"

    connector = TrustedNCSCConnector(
        exchange=SynapticExchange(registry=registry, authorization=policy),
        resolve_principal=resolver,
    )
    client = await connector(KEY)
    assert await client.request(ROOT, {"grants": {"invoke": [LEAF]}, "tenant_id": "spoof"}) == "ok"
    assert [(s.source, s.tenant_id) for s in observed] == [SCOPE[::-1], SCOPE[::-1]]
    with pytest.raises(EventPermissionError):
        await client.request(LEAF, {})
    assert policy.grants_for(EventPrincipal("agent", "other")) == SynapticGrants()


@pytest.mark.asyncio
async def test_session_is_rechecked_and_authority_errors_fail_closed():
    resolver, sessions, _, _ = setup_resolver()
    await resolver(KEY)
    sessions.get_session.return_value = replace(SESSION, active=False)
    with pytest.raises(EventPermissionError):
        await resolver(KEY)
    sessions.get_session.side_effect = RuntimeError("authority unavailable")
    with pytest.raises(RuntimeError, match="authority unavailable"):
        await resolver(KEY)
    resolver = AgentPrincipalResolver(
        sessions=UnboundAgentSessionAuthority(),
        definitions=SimpleNamespace(),
        authorization=SynapticAuthorization(),
    )
    with pytest.raises(EventPermissionError, match="not bound"):
        await resolver(KEY)


@pytest.mark.asyncio
async def test_approval_and_grant_configuration_are_snapshots():
    approvals = {SCOPE: APPROVAL}
    signals = {ROOT}
    grants = {SCOPE: SynapticGrants(signals)}
    policy = SynapticAuthorization(grants=grants)
    _, sessions, definitions, _ = setup_resolver()
    resolver = AgentPrincipalResolver(
        sessions=sessions, definitions=definitions, authorization=policy, approvals=approvals
    )
    approvals.clear()
    grants.clear()
    signals.add(LEAF)
    assert await resolver(KEY) == EventPrincipal("agent", "tenant")
    assert policy.grants_for(await resolver(KEY)).invoke == frozenset({ROOT})
    # Adding an approval after construction cannot authorize another resolver.
    with pytest.raises(EventPermissionError):
        await AgentPrincipalResolver(
            sessions=sessions, definitions=definitions, authorization=policy
        )(KEY)


def test_rejects_broker_grants_and_mis_scoped_approvals():
    with pytest.raises(TypeError):
        SynapticAuthorization(grants={SCOPE: EventGrants(publish={ROOT})})
    _, sessions, definitions, policy = setup_resolver()
    with pytest.raises(ValueError, match="scope mismatch"):
        AgentPrincipalResolver(
            sessions=sessions,
            definitions=definitions,
            authorization=policy,
            approvals={SCOPE: replace(APPROVAL, tenant_id="other")},
        )
