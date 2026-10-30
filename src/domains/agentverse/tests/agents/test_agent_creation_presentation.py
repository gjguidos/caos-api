"""Presentation describes the strict contract; it never executes creation."""

import pytest

from src.domains.agentverse.entities.agent_creation_presentation import (
    AgentCreationPresentation,
    agent_creation_presentation,
)
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput
from src.domains.agentverse.workflows.spec import WorkflowInputField

CONTRACT = "caos.agent_creation_draft"


def test_contract_fields_defaults_and_required_are_authoritative():
    spec = agent_creation_presentation(CONTRACT, 1)
    assert AgentCreationPresentation.model_validate(spec).presentation_version == 1
    fields = {field["key"]: field for field in spec["inputs"]}
    assert set(fields) == set(AgentCreationInput.model_fields)
    assert {key for key, field in fields.items() if field["required"]} == {
        "name",
        "type",
        "role_name",
        "personality_key",
        "prompt",
    }
    for key, field in fields.items():
        WorkflowInputField.model_validate(field)
        assert field["required"] == AgentCreationInput.model_fields[key].is_required()
    assert fields["visibility"]["default"] == "private"
    assert fields["tools"]["default"] == []
    assert spec["input_schema"] == AgentCreationInput.model_json_schema()
    assert spec["ui"]["submission"] is None
    assert spec["ui"]["navigation"] == "settings"


@pytest.mark.parametrize("contract,version", [(CONTRACT, 2), ("unknown", 1)])
def test_versions_fail_closed(contract, version):
    with pytest.raises(ValueError):
        agent_creation_presentation(contract, version)


def test_registry_sources_reuse_existing_requester_catalogs_without_tool_fields():
    spec = agent_creation_presentation(CONTRACT, 1)
    fields = {field["key"]: field for field in spec["inputs"]}
    assert {
        field["source"]["registry"]
        for field in fields.values()
        if field["source"] and field["source"]["kind"] == "registry"
    } == {"agent", "role", "personality", "tool", "workflow", "rulepack"}
    assert fields["tools"]["options"] is None
    assert fields["whitelisted_groups"]["source"] == {"kind": "requester_groups"}
    assert spec["ui"]["tool_configuration"]["fields"] == "ui.admin.fields"
