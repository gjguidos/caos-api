"""RG-02 client boundaries must not turn a known key into a read grant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.tests.test_utu import KEY, setup
from src.domains.agentverse.entities.conversation_v2 import ConversationV2
from src.domains.agentverse.entities.workflow import WorkflowCreateRequest
from src.domains.agentverse.exceptions import AgentAccessDeniedError, WorkflowNotFoundError
from src.domains.agentverse.interfaces.api.v1 import interface
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.agent_chat_service import AgentChatService
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.personality_service import PersonalityService
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.services.test_runtime_compatibility import definition

USER = User(id="owner", tenant_id="tenant", roles=["team.user"])


def catalog():
    regs = {kind: Registry(kind) for kind in RegistryService.CLIENT_KINDS}
    for reg in regs.values():
        for key, metadata in {
            "open": {"ownership": "global"},
            "secret": {"exposure": "internal"},
            "foreign": {"ownership": "tenant", "tenant_id": "other"},
            "local": {"ownership": "tenant", "tenant_id": "tenant"},
        }.items():
            reg.register(name=key, key=key, metadata=metadata, component=object)
    return RegistryService(regs)


@pytest.mark.parametrize("kind", sorted(RegistryService.CLIENT_KINDS))
def test_catalog_omits_protected_and_denies_direct_keys(kind):
    svc = catalog()
    assert svc.list_for_client(kind, USER) == ["open", "local"]
    assert [row["key"] for row in svc.list_for_client(kind, USER, include_metadata=True)] == [
        "open",
        "local",
    ]
    for key in ("secret", "foreign", "missing"):
        with pytest.raises(KeyError):
            svc.get_info_for_client(kind, key, USER)
    assert svc.get_info_for_client(kind, " OPEN ", USER).key == "open"
    # Client checks never modify shared registry metadata or internal resolution.
    assert svc.get_info(kind, "secret").metadata == {"exposure": "internal"}
    assert svc.get_component(kind, "secret") is object


@pytest.mark.parametrize("kind", ["synaptic", "tool_runtime_dependency", "unknown"])
def test_client_registry_allowlist_cannot_be_bypassed_with_a_known_key(kind):
    reg = Registry(kind)
    reg.register(name="open", component=object)
    svc = RegistryService({kind: reg})
    with pytest.raises(KeyError):
        svc.list_for_client(kind, USER)
    with pytest.raises(KeyError):
        svc.get_info_for_client(kind, "open", USER)
    assert svc.get_component(kind, "open") is object


@pytest.mark.parametrize(
    "user", [None, User(), User(id=""), {"id": "owner", "tenant_id": "tenant"}]
)
def test_missing_or_request_shaped_principal_cannot_read_even_public(user):
    svc = catalog()
    assert svc.list_for_client("tool", user) == []
    with pytest.raises(KeyError):
        svc.get_info_for_client("tool", "open", user)


@pytest.mark.parametrize("operation", ["DISCOVER", "READ"])
@pytest.mark.parametrize(
    "metadata",
    [
        {"exposure": "internal"},
        {"tags": ["SyStEm"]},
        {"tags": ["builtin"]},
        {"hidden": True},
        {"scope": "system", "exposure": "public"},
        {"tenant_id": "other"},
        {"tenant_scoped": True},
        {"tenant_id": ""},
        {"exposure": "invalid"},
    ],
)
def test_admin_and_forged_resource_identity_do_not_override_restrictions(operation, metadata):
    admin = User(id="owner", tenant_id="tenant", roles=["agentverse-admin"])
    assert not UTUAuthority.can_access_resource(
        admin, {**metadata, "principal_id": "owner", "user_id": "owner"}, operation=operation
    )


def test_tenant_policy_fails_closed_without_verified_tid_and_preserves_global_public_reads():
    legacy_user = User(id="owner")
    svc = catalog()
    assert svc.list_for_client("tool", legacy_user) == ["open"]
    assert UTUAuthority.can_access_resource(legacy_user, {"ownership": "global"}, operation="READ")
    assert not UTUAuthority.can_access_resource(
        legacy_user, {"tenant_id": "tenant"}, operation="READ"
    )
    assert not UTUAuthority.can_access_resource(USER, {"tenant_id": "other"}, operation="READ")


def test_private_resources_use_authenticated_owner_or_groups_after_tenant_checks():
    private = {"ownership": "tenant", "visibility": "private", "creator": "owner", "tenant_id": "tenant"}
    assert UTUAuthority.can_access_resource(USER, private, operation="READ")
    assert not UTUAuthority.can_access_resource(
        User(id="other", tenant_id="tenant"), private, operation="READ"
    )
    assert not UTUAuthority.can_access_resource(
        User(id="owner", tenant_id="other"), private, operation="READ"
    )


@pytest.mark.parametrize(
    "nested",
    [
        {"default_tools": ["secret"]},
        {"tools": [{"name": "secret", "provider_key": "tools:open"}]},
        {"personality_key": "secret"},
        {"role_name": "secret"},
        {"rule_pack_ids": ["secret"]},
        {"definition": {"steps": [{"type": "tool", "command": "@secret --topic=x"}]}},
        {"expanded": [{"metadata": {"exposure": "internal", "prompt": "secret"}}]},
        {"expanded": {"tenant_id": "other", "prompt": "secret"}},
        {"required_tools": ["missing"]},
    ],
)
def test_nested_protected_resources_deny_parent_and_omit_listing(nested):
    svc = catalog()
    svc._registries["role"].register(name="parent", metadata={"ownership": "global", **nested}, component=object)
    assert "parent" not in svc.list_for_client("role", USER)
    with pytest.raises(KeyError):
        svc.get_info_for_client("role", "parent", USER)
    assert svc.get_info("role", "parent").metadata == {"ownership": "global", **nested}


def test_public_nested_metadata_keeps_original_response_shape():
    svc = catalog()
    metadata = {"ownership": "global", "tools": [{"key": "open", "name": "open", "scope": "workflow"}], "custom": {"text": "hello"}}
    svc._registries["role"].register(name="parent", metadata=metadata, component=object)
    client = svc.get_info_for_client("role", "parent", USER)
    assert client.key == "parent"
    assert client.metadata["default_tools"] == [{"key": "open", "name": "open"}]
    assert "custom" not in client.metadata
    assert svc.get_info("role", "parent").metadata == metadata


@pytest.mark.asyncio
async def test_routes_pass_authenticated_user_and_keep_detail_error_envelope():
    svc = catalog()
    result = await interface.get_agent_types("tool", svc, USER)
    assert [row["key"] for row in result] == ["open", "local"]
    denied = await interface.get_registry_entry_details("tool", "secret", svc, USER)
    assert set(denied) == {"error"}
    assert "metadata" not in denied
    with pytest.raises(HTTPException) as error:
        await interface.get_agent_types("synaptic", svc, USER)
    assert error.value.status_code == 404


def test_soul_denied_before_instantiation_and_trusted_extraction_preserved(monkeypatch):
    from src.domains.agentverse.registries.registries import registries

    svc = catalog()
    for kind, reg in svc._registries.items():
        monkeypatch.setitem(registries, kind, reg)
    soul = PersonalityService()
    from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
        AgentSoulProtocol,
    )

    public_soul = AgentSoulProtocol()
    soul.extract_soul = Mock(return_value=public_soul)
    with pytest.raises(KeyError):
        soul.extract_soul_for_client("secret", USER)
    soul.extract_soul.assert_not_called()
    assert soul.extract_soul_for_client("open", USER).basic_profile == public_soul.basic_profile
    soul.extract_soul.assert_called_once_with("open")


@pytest.mark.asyncio
async def test_persisted_workflows_check_raw_policy_and_nested_tools_but_runtime_can_resolve(
    monkeypatch,
):
    from src.domains.agentverse.registries.registries import registries

    svc = catalog()
    monkeypatch.setitem(registries, "tool", svc._registries["tool"])
    rows = [
        {"ownership": "global", "key": "open", "name": "Open", "definition": {"steps": []}},
        {
            "key": "internal",
            "name": "Internal",
            "definition": {"steps": []},
            "exposure": "internal",
        },
        {"key": "foreign", "name": "Foreign", "definition": {"steps": []}, "tenant_id": "other"},
        {
            "key": "nested",
            "name": "Nested",
            "definition": {"steps": []},
            "required_tools": ["secret"],
        },
    ]
    db = SimpleNamespace(find_all=AsyncMock(return_value=rows), find_one=AsyncMock())
    workflow = WorkflowService(db)
    assert [row.key for row in await workflow.list_workflows(USER)] == ["open"]
    assert await workflow.list_workflows() == []
    for row in rows[1:]:
        db.find_one.return_value = row
        with pytest.raises(WorkflowNotFoundError):
            await workflow.get_workflow(row["key"], USER)
    # Trusted bulk lookup remains independent from the client read policy.
    db.find_all.return_value = rows[1:2]
    found, missing = await workflow.get_workflows(["internal"])
    assert found[0].exposure == "internal" and missing == []
    db.find_one.return_value = rows[0]
    assert (await workflow.get_workflow("open", USER)).key == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["public", "private"])
@pytest.mark.parametrize("roles", [[], ["team.user"], ["agentverse-admin"]])
async def test_internal_agent_is_never_client_readable_even_to_owner_admin_or_cached_chat(
    visibility, roles
):
    agent = definition(exposure="internal", visibility=visibility, whitelisted_groups=["team"])
    user = User(id="owner", tenant_id="tenant", roles=roles)
    db = SimpleNamespace(
        find_by_id=AsyncMock(return_value=agent.model_dump()),
        find_all=AsyncMock(return_value=[agent.model_dump()]),
    )
    factory = SimpleNamespace(build_agent=AsyncMock())
    service = AgentService(factory, db, None, None)
    assert not agent_access.can_read(user, agent)
    assert await service.list_agents(user) == []
    with pytest.raises(AgentAccessDeniedError):
        await service.get_agent(agent.id, user)
    chat = AgentChatService.__new__(AgentChatService)
    chat.agent_service = service
    chat.instance_registry = Mock()
    with pytest.raises(AgentAccessDeniedError):
        await chat.get_or_spawn_agent(None, None, "s", agent.id, user)
    chat.instance_registry.get.assert_not_called()
    factory.build_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_direct_id_mismatch_and_cross_tenant_denied_before_runtime():
    agent = definition(ownership="tenant", tenant_id="other")
    service = AgentService(
        Mock(), SimpleNamespace(find_by_id=AsyncMock(return_value=agent.model_dump())), None, None
    )
    with pytest.raises(AgentAccessDeniedError):
        await service.get_agent(agent.id, USER)
    agent.tenant_id = "tenant"
    service.db_service.find_by_id.return_value = agent.model_dump()
    assert (await service.get_agent(agent.id, USER)).id == agent.id
    with pytest.raises(AgentAccessDeniedError):
        await service.get_agent("forged-id", USER)


@pytest.mark.asyncio
async def test_internal_trusted_v2_principal_resolution_survives_but_conversation_entry_and_detail_deny():
    env = setup()
    env.definitions.find_by_id.return_value = {
        "id": "agent",
        "creator": "owner",
        "exposure": "internal",
        "tenant_id": "tenant",
    }
    principal = await env.utu.resolve_agent_principal(KEY)
    assert principal.agent_id == "agent"
    conversation = ConversationV2(
        tenant_id="tenant", member_user_ids=("owner",), agent_ids=("agent",)
    )
    for targets in [(), ("agent",)]:
        with pytest.raises(EventPermissionError, match="target denied"):
            await env.utu.authorize_conversation(
                session_id="session", conversation=conversation, agent_ids=targets
            )
    env.factory.build.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_create_ignores_payload_tenant_and_uses_authenticated_tenant():
    db = SimpleNamespace(
        find_one=AsyncMock(return_value=None), repository=SimpleNamespace(create=AsyncMock())
    )
    svc = WorkflowService(db)
    svc._assert_valid = Mock(return_value=[])
    request = WorkflowCreateRequest.model_validate(
        {"name": "flow", "definition": {}, "tenant_id": "forged", "exposure": "internal"}
    )
    result = await svc.create_workflow(request, USER)
    assert result.tenant_id == "tenant" and result.exposure == "public"
    assert db.repository.create.call_args.args[0]["tenant_id"] == "tenant"


@pytest.mark.parametrize("field", ["tenant_id", "exposure", "creator", "user_id", "id"])
def test_agent_patch_cannot_rewrite_server_owned_identity_or_exposure(field):
    from pydantic import ValidationError

    from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest

    with pytest.raises(ValidationError, match="managed by the server"):
        AgentUpdateRequest.model_validate({field: "forged"})


def test_internal_and_cross_tenant_agents_cannot_leak_through_write_response():
    admin = User(id="owner", tenant_id="tenant", roles=["agentverse-admin"])
    assert not agent_access.can_write(admin, definition(exposure="internal"))
    assert not agent_access.can_write(admin, definition(tenant_id="other"))
    assert agent_access.can_write(admin, definition(ownership="tenant", tenant_id="tenant"))


@pytest.mark.asyncio
async def test_protected_workflow_patch_and_delete_do_not_bypass_read_policy():
    from src.domains.agentverse.entities.workflow import WorkflowUpdateRequest

    db = SimpleNamespace(
        find_one=AsyncMock(
            return_value={"key": "flow", "name": "Flow", "definition": {}, "exposure": "internal"}
        ),
        repository=SimpleNamespace(update=AsyncMock(), delete=AsyncMock()),
    )
    svc = WorkflowService(db)
    for operation in [
        svc.update_workflow("flow", WorkflowUpdateRequest(), USER),
        svc.delete_workflow("flow", USER),
    ]:
        with pytest.raises(WorkflowNotFoundError):
            await operation
    db.repository.update.assert_not_awaited()
    db.repository.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tid", [None, "tenant"])
async def test_http_identity_and_tenant_come_only_from_verified_claims(tid):
    import httpx
    from fastapi import FastAPI, Request

    from src.base.middlewares.jwt_middleware import JWTVerificationMiddleware

    claims = {"oid": "owner", "tid": tid, "roles": []}
    jwt = SimpleNamespace(verify_token=AsyncMock(return_value=claims))
    app = FastAPI()
    app.add_middleware(JWTVerificationMiddleware, jwt_service=jwt)

    @app.post("/probe")
    async def probe(request: Request):
        return {"id": request.state.user.id, "tenant": request.state.user.tenant_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/probe?tenant_id=forged&principal_id=forged",
            headers={"authorization": "Bearer verified", "x-tenant-id": "forged"},
            json={"tenant_id": "forged", "principal": {"id": "forged"}},
        )
    assert result.json() == {"id": "owner", "tenant": tid}
    jwt.verify_token.assert_awaited_once_with("verified")


@pytest.mark.asyncio
async def test_v1_socket_uses_verified_tenant_not_query_or_header(monkeypatch):
    from src.base.decorators.middleware import ws_session_based as ws_module

    monkeypatch.setattr(
        ws_module,
        "authenticate_websocket",
        AsyncMock(return_value={"oid": "owner", "tid": "tenant"}),
    )
    monkeypatch.setattr(ws_module, "get_session_key", Mock(return_value="session"))
    ws = SimpleNamespace(
        headers={"Authorization": "Bearer verified", "x-tenant-id": "forged"},
        query_params={"tenant_id": "forged"},
        scope={},
        url=SimpleNamespace(path="/api/v1/agentverse/ws"),
        accept=AsyncMock(),
    )
    await ws_module._authenticate_and_accept(ws, None, None, None)
    assert (ws.scope["user"].id, ws.scope["user"].tenant_id) == ("owner", "tenant")


def test_expanded_soul_cannot_return_protected_nested_payload(monkeypatch):
    from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
        AgentSoulProtocol,
    )
    from src.domains.agentverse.registries.registries import registries

    svc = catalog()
    monkeypatch.setitem(registries, "personality", svc._registries["personality"])
    soul = PersonalityService()
    soul.extract_soul = Mock(
        return_value=AgentSoulProtocol(
            expanded={"metadata": {"exposure": "internal", "prompt": "secret"}}
        )
    )
    with pytest.raises(KeyError):
        soul.extract_soul_for_client("open", USER)


def test_schema_field_names_are_descriptions_not_resource_identity():
    svc = catalog()
    metadata = {
        "ownership": "global",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "exposure": {"type": "string"},
                "tenant_id": {"type": "string"},
                "tool_key": {"type": "string"},
            },
        }
    }
    svc._registries["tool"].register(name="schema", metadata=metadata, component=object)
    projected = svc.get_info_for_client("tool", "schema", USER).metadata
    assert projected["parameters_schema"]["type"] == "object"
    assert projected["parameters_schema"]["properties"] == {}
    assert svc.get_info("tool", "schema").metadata == metadata


@pytest.mark.parametrize("command", [" @secret --x=1", "\n@secret --x=1", "@secret--x=1"])
def test_workflow_catalog_uses_runtime_command_key_parser(command):
    svc = catalog()
    svc._registries["workflow"].register(
        name="flow",
        metadata={"ownership": "global", "definition": {"steps": [{"type": "tool", "command": command}]}},
        component=object,
    )
    with pytest.raises(KeyError):
        svc.get_info_for_client("workflow", "flow", USER)


@pytest.mark.asyncio
async def test_agent_create_persists_authenticated_tenant_and_never_payload_exposure():
    from src.domains.agentverse.tests.services.test_creation_governance import config, service

    svc = service()
    svc.agent_factory.create_agent.return_value = definition()

    async def store(_emitter, agent):
        return agent.model_dump()

    svc.db_service.store_agent.side_effect = store
    result = await svc.create_agent(
        Mock(),
        SimpleNamespace(),
        config(tenant_id="forged", exposure="internal"),
        User(id="owner", tenant_id="tenant", roles=["agentverse-admin"]),
    )
    assert result.tenant_id == "tenant" and result.exposure == "public"


def test_internal_tool_upload_metadata_cannot_bypass_catalog_gate(monkeypatch):
    from src.domains.agentverse.interfaces.api.v1 import file_routes

    svc = catalog()
    reg = svc._registries["tool"]
    reg.get_metadata_for_ref = Mock(
        return_value={"supports": {"files": {"accepted_mimes": ["text/plain"]}}}
    )
    monkeypatch.setattr(file_routes, "tool_registry_instance", reg)
    with pytest.raises(HTTPException) as error:
        file_routes._resolve_files_support("secret", USER)
    assert error.value.status_code == 400
    reg.get_metadata_for_ref.assert_not_called()
    assert file_routes._resolve_files_support("open", USER)[0] == "open"
