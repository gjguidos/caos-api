"""Approved form/enki composition remains independent of invocation authority."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.composition.models import CURRENT_V2_POLICY
from src.domains.agentverse.agents.roles.enki.draft import AgentCreationDraft
from src.domains.agentverse.agents.roles.enki.spec import role_spec
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.tests.agents import test_agent_composition_v2 as composition
from src.domains.agentverse.tests.agents import test_genesis_initiation as initiation
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator


def test_enki_role_is_operational_declarative_and_grants_nothing():
    assert role_spec.key == "enki"
    assert (role_spec.agent_group, role_spec.agent_subclass) == ("interactive_ui", "operational")
    assert not role_spec.handlers and not role_spec.default_tools
    assert not role_spec.capabilities["required_tools"]


def test_enki_role_registers_through_standard_module_discovery():
    import importlib

    from src.domains.agentverse.registries import role_registry_instance

    importlib.import_module("src.domains.agentverse.agents.roles.enki.enki")
    assert role_registry_instance.get_role("enki").agent_subclass == "operational"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,valid", [("enki", True), ("general", False)])
async def test_form_role_compatibility_is_not_bypassed(role, valid):
    registries = {k: Registry(k) for k in ("agent", "role", "personality", "tool", "rulepack")}
    registries["agent"].register(
        "Form",
        key="form",
        component=object,
        metadata={
            "ownership": "global",
            "agent_group": "interactive_ui",
            "agent_subclass": "operational",
        },
    )
    registries["role"].register(
        "Enki", key="enki", component=object, metadata=role_spec.to_metadata()
    )
    registries["role"].register(
        "General",
        key="general",
        component=object,
        metadata={
            "ownership": "global",
            "agent_group": "interactive_ui",
            "agent_subclass": "conversational",
        },
    )
    registries["personality"].register(
        "Empty", key="empty_eva", component=object, metadata={"ownership": "global"}
    )
    validator = AgentDefinitionValidator(registries=registries, workflow_service=SimpleNamespace())
    result = await validator.validate(
        composition.normalized(
            type="form", role_name=role, tools=[], workflows=[], rule_pack_ids=[]
        ),
        requester=User(id=composition.SUBJECT, tenant_id=composition.TENANT),
    )
    assert result.valid is valid, result.issues
    if not valid:
        assert "composition.role_type_mismatch" in {i.code for i in result.issues}


@pytest.mark.parametrize("role,accepted", [("enki", True), ("general", False), ("crawler", False)])
def test_v2_composition_allows_only_approved_conversational_form(role, accepted):
    result = composition.composer().compose(
        composition.validated(composition.normalized(type="form", role_name=role)),
        context=composition.CONTEXT,
        policy=CURRENT_V2_POLICY,
    )
    assert result.composed is accepted
    if accepted:
        assert result.candidate["type"] == "form" and result.candidate["role_name"] == "enki"
        assert result.candidate["runtime_version"] == "v2" and result.candidate["llm_type"]


def test_form_enki_still_requires_real_cognitive_resources():
    factory = composition.catalog()
    factory.defaults["llm"] = "missing"
    result = composition.composer(factory).compose(
        composition.validated(composition.normalized(type="form", role_name="enki")),
        context=composition.CONTEXT,
        policy=CURRENT_V2_POLICY,
    )
    assert not result.composed


@pytest.mark.asyncio
async def test_historical_chat_success_is_denied_before_terminal_replay():
    s = await initiation.environment()
    first = await initiation.initiate(s)
    assert first["status"] == "provisioned"
    pinned = initiation.genesis.ENKI_SLOT.model_copy(update={"type": "form"})
    s.adapter._provisions = MappingProxyType({"enki": pinned})
    s.provisioner.provision = AsyncMock(
        side_effect=AssertionError("must not replay historical chat success")
    )
    with pytest.raises(EventPermissionError, match="pinned definition"):
        await initiation.initiate(s)
    assert len(s.logs.documents) == 4 and len(s.persistence.documents) == 1


def test_draft_contract_is_versioned_and_never_accepts_authority_fields():
    body = {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": composition.normalized(name="New Agent"),
    }
    assert AgentCreationDraft.model_validate(body).creation.name == "New Agent"
    for change in (
        {"schema_version": 2},
        {"permit": {}},
        {"authorization": {}},
        {"operation_id": "x"},
    ):
        with pytest.raises(ValidationError):
            AgentCreationDraft.model_validate({**body, **change})
    with pytest.raises(ValidationError):
        AgentCreationDraft.model_validate(
            {**body, "creation": {**body["creation"], "creator": "forged"}}
        )
