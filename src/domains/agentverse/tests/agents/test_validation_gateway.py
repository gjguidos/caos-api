"""Read-only gateway: real UTU/CPBX, validator, catalogs and composer; no runtime tools."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.interfaces.api.v2 import genesis_agents
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY, VALIDATE, environment
from src.domains.agentverse.security.validation_gateway import (
    AgentDraftValidationGateway,
    AgentDraftValidationRequest,
    ValidationGatewayBinding,
)
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.agents.test_agent_composition_v2 import catalog as backend_catalog
from src.domains.agentverse.tests.tools.test_data_validation_tool import (
    FakeWorkflowDB,
    catalog,
    creation,
)
from src.domains.agentverse.tools.validation_tool import register_data_validation
from src.domains.agentverse.tools.validation_tool.dependencies.di_container import (
    ValidationToolContainer,
)


def request(**overrides):
    return AgentDraftValidationRequest(
        contract="caos.agent_creation_draft",
        schema_version=1,
        resource_type="agent",
        creation=creation(
            name="Review Candidate",
            role_name="general",
            tools=[],
            workflows=[],
            rule_pack_ids=[],
            **overrides,
        ),
    )


@pytest.fixture
def stack(monkeypatch):
    from src.domains.agentverse.registries.registries import registries

    env = environment()
    catalogs = catalog()
    for kind, registry in catalogs.items():
        monkeypatch.setitem(registries, kind, registry)
    source = {
        "id": "source",
        "ownership": "tenant",
        "tenant_id": KEY.tenant_id,
        "creator": KEY.user_id,
        "visibility": "private",
    }
    definitions = SimpleNamespace(find_by_id=AsyncMock(return_value=source))
    container = ValidationToolContainer(
        registries=providers.Object(catalogs),
        workflow_service=providers.Object(WorkflowService(FakeWorkflowDB([]))),
        utu_authority=providers.Object(env.authority),
    )
    env.registry.unregister(VALIDATE.signal)
    register_data_validation(env.registry, container.tool())
    binding = ValidationGatewayBinding(
        tenant_id=KEY.tenant_id,
        subject_id=KEY.user_id,
        source_agent_id="source",
        executor_id=KEY.agent_id,
    )
    composer = AgentComposer(catalog=backend_catalog)
    gateway = AgentDraftValidationGateway(
        bindings=(binding,),
        authority=env.authority,
        exchange=env.exchange,
        read_definition=definitions.find_by_id,
        composer=composer,
    )
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_gateway_validates_composes_without_writes_activation_or_inference(stack):
    s = stack
    result = await s.gateway.validate(s.env.session, "source", request())
    assert result["validation"]["valid"]
    assert result["validation"]["resolved"] is None
    assert result["composition"]["compatible"]
    assert result["composition"]["preview"]["gui_components"][0]["type"] == "chat"
    assert result["composition"]["runtime_execution_authorized"] is False
    assert result["capability"] == {
        "signal": "data.validation",
        "provider": "system.validation",
        "version": "1.0.0",
        "operation": "agent.creation_draft.validate",
    }
    assert (
        not {"llm_type", "db_type", "tenant_id", "creator"}
        & result["composition"]["preview"].keys()
    )


@pytest.mark.asyncio
async def test_missing_binding_unreadable_source_and_bootstrap_fail_closed(stack):
    s = stack
    with pytest.raises(EventPermissionError):
        await s.gateway.validate(s.env.session, "other-source", request())
    s.source["tenant_id"] = "other"
    with pytest.raises(EventPermissionError):
        await s.gateway.validate(s.env.session, "source", request())
    from dataclasses import replace

    with pytest.raises(EventPermissionError):
        await s.gateway.validate(replace(s.env.session, origin="bootstrap"), "source", request())


@pytest.mark.asyncio
async def test_group_private_catalogs_and_validator_have_the_same_resource_requester(stack):
    s = stack
    user = User(id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["team.user"])
    s.env.sessions.resource_requester.return_value = user
    for kind, key in (("agent", "chat"), ("role", "general"), ("personality", "empty_eva")):
        s.catalogs[kind].get_info(key).metadata.update(
            ownership="tenant",
            tenant_id=KEY.tenant_id,
            visibility="private",
            creator="different-owner",
            whitelisted_groups=["team"],
        )
    catalog_service = RegistryService(s.catalogs)
    assert "general" in catalog_service.list_for_client("role", user)
    result = await s.gateway.validate(s.env.session, "source", request(whitelisted_groups=["team"]))
    assert result["validation"]["valid"], result
    s.env.sessions.resource_requester.return_value = User(id=KEY.user_id, tenant_id=KEY.tenant_id)
    assert "general" not in catalog_service.list_for_client(
        "role", s.env.sessions.resource_requester.return_value
    )
    denied = await s.gateway.validate(s.env.session, "source", request(whitelisted_groups=["team"]))
    assert not denied["validation"]["valid"]


@pytest.mark.asyncio
async def test_registry_valid_is_not_the_same_as_v2_composable(stack):
    s = stack
    payload = request()
    payload.creation.update(role_name="writer", tools=[{"key": "probe", "config": {}}])
    result = await s.gateway.validate(s.env.session, "source", payload)
    assert result["validation"]["valid"]
    assert not result["composition"]["compatible"]
    assert result["composition"]["preview"] is None
    assert result["composition"]["issues"][0]["code"] == "composition.role_tools_unsupported"


@pytest.mark.asyncio
async def test_http_contract_rejects_security_overrides_and_missing_authorization(
    stack, monkeypatch
):
    s = stack

    @asynccontextmanager
    async def authenticated(_request):
        yield s.env.session

    monkeypatch.setattr(genesis_agents, "authenticated_genesis", authenticated)
    app = FastAPI()
    app.state.agent_draft_validation_gateway = s.gateway
    app.include_router(genesis_agents.router)
    path = "/api/v2/agentverse/agents/source/creation-draft/validate"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for field in (
            "executor_id",
            "requester",
            "tenant_id",
            "roles",
            "scope",
            "signal",
            "version",
        ):
            response = await client.post(path, json={**request().model_dump(), field: "forged"})
            assert response.status_code == 422
        # The approved draft contract is exact: resource_type is required and fixed.
        draft = request().model_dump()
        for resource_type in (None, "workflow", "store"):
            body = {k: v for k, v in draft.items() if k != "resource_type" or resource_type}
            if resource_type:
                body["resource_type"] = resource_type
            assert (await client.post(path, json=body)).status_code == 422
        assert (await client.post(path, json=request().model_dump())).status_code == 200
        del app.state.agent_draft_validation_gateway
        assert (await client.post(path, json=request().model_dump())).status_code == 403
