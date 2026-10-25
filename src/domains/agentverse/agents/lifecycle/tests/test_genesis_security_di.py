from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dependency_injector import providers

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    VerifiedAgentSession,
)
from src.domains.agentverse.dependencies.di_container import AgentverseContainer, extend_container
from src.domains.agentverse.dependencies.genesis_security import GenesisSecurityBindings
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

KEY = AgentInstanceKey("tenant", "user", "agent", "session")


def base_container():
    # Only infrastructure providers are supplied; constructing DI performs no IO.
    return SimpleNamespace(
        **{
            name: SimpleNamespace(**{attribute: providers.Object(object())})
            for name, attribute in (
                ("browser_session", "browser_session_service"),
                ("database", "mongodb_repository"),
                ("redis", "redis_repository"),
                ("http", "mcp_proxy_client"),
                ("blob_storage", "file_service"),
                ("events", "domain_event_publisher"),
            )
        }
    )


@pytest.mark.asyncio
async def test_default_container_has_fail_closed_resolver_and_v1_providers():
    container = AgentverseContainer()
    container.mongodb_repository.override(providers.Object(object()))
    container.redis_repository.override(providers.Object(object()))
    assert container.agent_service is not None
    assert container.agent_chat_service is not None
    with pytest.raises(EventPermissionError, match="not bound"):
        await container.agent_principal_resolver()(KEY)


@pytest.mark.asyncio
async def test_extend_binds_shared_policy_and_session_authority():
    authority = SimpleNamespace(
        bind_revocation_handler=Mock(),
        get_session=AsyncMock(
            return_value=VerifiedAgentSession("tenant", "user", "session", {"agent"}, active=True)
        ),
    )
    policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants({"chat.respond"})})
    bindings = GenesisSecurityBindings(
        sessions=authority,
        authorization=policy,
        approvals={("tenant", "agent"): AgentActivationApproval("tenant", "agent", "user", True)},
    )
    infrastructure = base_container()
    infrastructure.database.mongodb_repository.override(
        providers.Object(
            SimpleNamespace(find_one=AsyncMock(return_value={"id": "agent", "creator": "user"}))
        )
    )
    base = extend_container(infrastructure, genesis_security=bindings)
    assert base.agentverse.synaptic_authorization() is policy
    assert base.agentverse.genesis_sessions() is authority
    assert base.agentverse.synaptic_exchange() is base.agentverse.synaptic_exchange()
    # Test the actual resolver constructed by DI with the real DBService API.
    from src.domains.agentverse.security.revocation import policy_version

    infrastructure.redis.redis_repository.override(
        providers.Object(
            SimpleNamespace(
                eval=AsyncMock(return_value=[policy_version(policy), "incarnation", "0", "0"])
            )
        )
    )
    # The provider was already resolved above; replace the test repository explicitly.
    base.agentverse.utu_authority()._revocations._repo = infrastructure.redis.redis_repository()
    resolver = base.agentverse.agent_principal_resolver()
    assert (await resolver(KEY)).agent_id == "agent"
    assert base.agentverse.trusted_ncsc_connector() is not None
    assert base.agentverse.instance_registry is not None


def test_custom_bindings_remain_lazy_and_v1_call_still_supported():
    async def resolver(key):
        raise AssertionError("resolver invoked during composition")

    exchange = object()
    base = extend_container(
        base_container(), synaptic_exchange=exchange, agent_principal_resolver=resolver
    )
    assert base.agentverse.synaptic_exchange() is exchange
    assert base.agentverse.agent_principal_resolver() is resolver
    assert extend_container(base_container()).agentverse.instance_registry is not None
