"""Persisted runtime contracts must reject V2 before V1 construction or reuse."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.handlers.exception import classify_error
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.exceptions import AgentAccessDeniedError
from src.domains.agentverse.runtime.compatibility import UnsupportedAgentRuntimeError
from src.domains.agentverse.services.agent_chat_service import AgentChatService
from src.domains.agentverse.services.agent_service import AgentService


def definition(**changes):
    return Agent(
        **{
            "id": "arbitrary-id",
            "creator": "owner",
            "name": "Empty EVA",
            "system_name": "empty_eva",
            "type": "chat",
            "prompt": "test",
            "chat_url": "",
            "llm_type": "openai",
            "audio_type": "openai",
            "db_type": "mongodb",
            "cache_type": "redis",
            "knowledge_db_type": "chromadb",
            "messaging_type": "rabbitmq",
            "dna_sequence": "test",
            **changes,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "v1", "v2"])
async def test_service_runtime_boundary_preserves_legacy_empty_eva(version):
    agent = definition(**({"runtime_version": version} if version else {}))
    factory = SimpleNamespace(
        build_agent=AsyncMock(return_value=SimpleNamespace(id=agent.id, name=agent.name))
    )
    service = AgentService(factory, None, None, None)
    service.get_agent = AsyncMock(return_value=agent)
    args = dict(
        socket_emitter=None,
        request=None,
        agent_id=agent.id,
        user=SimpleNamespace(id="owner"),
        socket_id="session",
    )
    if version == "v2":
        with pytest.raises(UnsupportedAgentRuntimeError) as error:
            await service.get_runtime_agent(**args)
        assert classify_error(error.value)[1] == "UNSUPPORTED_AGENT_RUNTIME"
        assert "/v2/conversations" in str(error.value)
        factory.build_agent.assert_not_awaited()
    else:
        assert await service.get_runtime_agent(**args) is factory.build_agent.return_value
        factory.build_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_factory_rejects_renamed_v2_before_any_provider_or_role_resolution():
    factory = AgentFactory.__new__(AgentFactory)
    factory._resolve_runtime_resources = Mock()
    with pytest.raises(UnsupportedAgentRuntimeError):
        await factory.build_agent(None, None, definition(runtime_version="v2"), "owner", "s")
    factory._resolve_runtime_resources.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_v1_cache_cannot_bypass_persisted_runtime_or_access(denied):
    chat = AgentChatService.__new__(AgentChatService)
    chat.instance_registry = Mock()
    chat.agent_service = SimpleNamespace(
        get_agent=AsyncMock(
            return_value=definition(runtime_version="v2"),
            side_effect=AgentAccessDeniedError("other", "id", "read") if denied else None,
        )
    )
    with pytest.raises(AgentAccessDeniedError if denied else UnsupportedAgentRuntimeError):
        await chat.get_or_spawn_agent(None, None, "s", "id", SimpleNamespace(id="other"))
    chat.instance_registry.get.assert_not_called()


@pytest.mark.parametrize("field", ["runtime_version", "genesis_local_seed"])
def test_clients_cannot_rewrite_runtime_or_bootstrap_provenance(field):
    from pydantic import ValidationError

    from src.domains.agentverse.entities.agent_request import AgentRequest
    from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest

    for model in (AgentRequest, AgentUpdateRequest):
        with pytest.raises(ValidationError, match="managed by the server"):
            model.model_validate({field: "v1"})
