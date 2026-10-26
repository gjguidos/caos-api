"""Creation authority is enforced before any resource work at the shared boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from src.base.entities.user import User
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.exceptions import AgentAccessDeniedError
from src.domains.agentverse.interfaces.api.v1.websockets.routes import ws_chat_handler, ws_handler
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.divine_orchestration_service import DivineOrchestrationService


def config(**extra):
    return AgentRequest(
        user_id="forged-owner", name="Governance EVA", type="chat", prompt="Hello", **extra
    )


def service():
    svc = AgentService(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    svc.db_service.check_for_duplicates = AsyncMock()
    svc.db_service.store_agent = AsyncMock()
    svc.agent_factory.create_agent = AsyncMock()
    svc._validate_and_prepare_config = AsyncMock()
    svc.try_log_operation = AsyncMock()
    return svc


def assert_no_work(svc):
    svc.db_service.check_for_duplicates.assert_not_awaited()
    svc.db_service.store_agent.assert_not_awaited()
    svc.agent_factory.create_agent.assert_not_awaited()
    svc._validate_and_prepare_config.assert_not_awaited()
    svc.try_log_operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        User(id="ordinary", roles=["agentverse-user"]),
        User(id="ordinary", roles=["team.user"]),
        User(roles=["agentverse-admin"]),
        {"id": "fake", "roles": ["agentverse-admin"]},
    ],
)
async def test_direct_service_denies_before_all_work(user):
    svc = service()
    with pytest.raises(AgentAccessDeniedError) as error:
        await svc.create_agent(MagicMock(), SimpleNamespace(), config(), user)
    assert error.value.status_code == 403
    assert_no_work(svc)


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [["agentverse-admin"], ["team.admin"]])
async def test_admin_reaches_factory_with_authenticated_owner(roles):
    svc = service()
    # Stop at the factory, proving authorization and owner normalization without
    # constructing a real agent or touching a database.
    svc.agent_factory.create_agent.side_effect = RuntimeError("factory reached")
    request = config(whitelisted_groups=["team"] if "team.admin" in roles else [])
    with pytest.raises(RuntimeError, match="factory reached"):
        await svc.create_agent(
            MagicMock(), SimpleNamespace(), request, User(id="trusted", tenant_id="tenant", roles=roles)
        )
    svc.agent_factory.create_agent.assert_awaited_once()
    assert request.user_id == "trusted"
    svc.db_service.store_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_admin_still_cannot_whitelist_foreign_groups():
    svc = service()
    with pytest.raises(ValueError, match="cannot whitelist"):
        await svc.create_agent(
            MagicMock(),
            SimpleNamespace(),
            config(whitelisted_groups=["foreign"]),
            User(id="trusted", tenant_id="tenant", roles=["team.admin"]),
        )
    assert_no_work(svc)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [ws_handler, ws_chat_handler])
@pytest.mark.parametrize("roles", [["agentverse-user"], ["agentverse-admin"], ["team.admin"]])
async def test_each_v1_socket_enforces_authenticated_creation_authority(handler, roles):
    svc = service()
    chat = MagicMock()
    chat.test_agent = AsyncMock(return_value={"status": "success"})
    allowed = roles != ["agentverse-user"]
    agent = Agent(
        id="created",
        creator="ordinary",
        name="Governance EVA",
        system_name="governance_eva",
        type="chat",
        prompt="Hello",
        chat_url="/chat/created",
        llm_type="noop",
        audio_type="noop",
        db_type=None,
        cache_type=None,
        knowledge_db_type=None,
        messaging_type=None,
        dna_sequence="test",
    )
    svc.agent_factory.create_agent.return_value = agent
    svc.db_service.store_agent.return_value = agent.model_dump()
    dos = DivineOrchestrationService(svc, chat)
    ws = MagicMock()
    ws.scope = {"user": User(id="ordinary", tenant_id="tenant", roles=roles)}
    ws.send_json = AsyncMock()
    bridge = MagicMock()
    bridge.connect = AsyncMock()
    bridge.subscribe = AsyncMock()
    bridge.disconnect = AsyncMock()
    ws.app.state.cognitive_modules = {"communication": {"socketRedisBridge": bridge}}
    ws.app.container.agentverse.divine_orchestration_service.return_value = dos
    # No external authz client is configured. The authenticated socket bodies
    # exercise real event registration, orchestration and the shared service.
    ws.app.container.authz.authz_client.return_value = None
    completed = asyncio.Event()

    async def send(payload):
        if (payload.get("response") or {}).get("status") in {"ERROR", "OK"}:
            completed.set()

    ws.send_json.side_effect = send
    message = config().model_dump() | {
        "roles": ["agentverse-admin"],
        "tenant_id": "forged",
        "grants": ["agent.create"],
        "principal": {"roles": ["agentverse-admin"]},
    }
    frame = {
        "event": "joshu-a.create",
        "data": {
            "message": message,
            "principal": {"roles": ["agentverse-admin"]},
            "tenant_id": "forged",
            "grants": ["agent.create"],
        },
    }
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return frame
        await asyncio.wait_for(completed.wait(), timeout=2)
        raise WebSocketDisconnect()

    ws.receive_json = receive
    if handler is ws_handler:
        await handler.__wrapped__(ws)
    else:
        await handler.__wrapped__(ws, "joshu-a")
    assert completed.is_set()
    terminal = [
        call.args[0]["response"]
        for call in ws.send_json.call_args_list
        if (call.args[0].get("response") or {}).get("status") in {"ERROR", "OK"}
    ]
    assert len(terminal) == 1
    if allowed:
        assert terminal[0]["status"] == "OK"
        svc.agent_factory.create_agent.assert_awaited_once()
        assert svc.agent_factory.create_agent.call_args.args[1].user_id == "ordinary"
        svc.db_service.store_agent.assert_awaited_once()
        chat.test_agent.assert_awaited_once()
    else:
        assert terminal[0]["status"] == "ERROR"
        assert terminal[0]["agent"] is None
        assert_no_work(svc)
        chat.test_agent.assert_not_awaited()
