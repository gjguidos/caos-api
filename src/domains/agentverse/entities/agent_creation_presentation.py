"""Versioned, read-only presentation of the authoritative creation input.

Sources reference the existing requester-filtered catalogs, never a second registry.
Tool config belongs to each Tool's config schema and ``ui.admin.fields``.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.domains.agentverse.entities.gui_components import AGENT_CREATION_DRAFT_CONTRACT
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput
from src.domains.agentverse.workflows.spec import WorkflowInputField


class AgentCreationPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.agent_creation_draft"]
    schema_version: Literal[1]
    presentation_version: Literal[1]
    resource_type: Literal["agent"]
    inputs: list[WorkflowInputField]
    input_schema: dict[str, Any]
    ui: dict[str, Any]


def agent_creation_presentation(contract: str, schema_version: int) -> dict[str, Any]:
    if contract != AGENT_CREATION_DRAFT_CONTRACT or schema_version != 1:
        raise ValueError("Unsupported Agent creation presentation contract/version")

    declarations = [
        ("name", "Agent name", "text", "identity", None),
        ("type", "Agent type", "select", "identity", "agent"),
        ("role_name", "Role", "select", "identity", "role"),
        ("personality_key", "Agent Soul", "select", "behaviour", "personality"),
        ("tools", "Tools", "multiselect", "capabilities", "tool"),
        ("workflows", "Workflows", "multiselect", "capabilities", "workflow"),
        ("rule_pack_ids", "RulePacks", "multiselect", "behaviour", "rulepack"),
        ("visibility", "Visibility", "select", "access", None),
        ("whitelisted_groups", "Allowed groups", "multiselect", "access", None),
        ("prompt", "Instructions", "textarea", "behaviour", None),
        ("gui_components", "GUI components (optional)", "object", "identity", None),
    ]
    schema = AgentCreationInput.model_json_schema()
    fields = []
    for key, label, widget, section, registry in declarations:
        model_field = AgentCreationInput.model_fields[key]
        prop = schema["properties"][key]
        source = None
        if registry:
            source = {
                "kind": "registry",
                "registry": registry,
                "requester_aware": True,
            }
        elif key == "whitelisted_groups":
            source = {"kind": "requester_groups"}
        field = WorkflowInputField(
            key=key,
            label=label,
            input=widget,
            required=model_field.is_required(),
            default=None
            if model_field.is_required()
            else model_field.get_default(call_default_factory=True),
            constraints={k: prop[k] for k in ("minLength", "maxLength", "pattern") if k in prop},
            options=[{"value": v, "label": v.title()} for v in prop["enum"]]
            if "enum" in prop
            else None,
            source=source,
            section=section,
        )
        fields.append(field.model_dump(mode="json"))
    return AgentCreationPresentation.model_validate(
        {
            "contract": contract,
            "schema_version": schema_version,
            "presentation_version": 1,
            "resource_type": "agent",
            "inputs": fields,
            "input_schema": schema,
            "ui": {
                "navigation": "settings",
                "summary": "live",
                "validation": "local_review_only",
                "submission": None,
                "tool_configuration": {
                    "source": "tool",
                    "schema": "agent_config_schema",
                    "fields": "ui.admin.fields",
                },
            },
        }
    ).model_dump(mode="json")
