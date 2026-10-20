"""Tests for save-time workflow definition validation (WS-C)."""

from __future__ import annotations

# Register the tools the sample workflows reference.
import src.domains.agentverse.tools.draw_tool  # noqa: F401
import src.domains.agentverse.tools.writer_tool  # noqa: F401
from src.domains.agentverse.workflows.helpers.validate_definition import (
    validate_workflow_definition,
)
from src.domains.agentverse.workflows.spec import WorkflowInputField


def test_valid_definition_passes():
    definition = {
        "name": "OK",
        "execution_mode": "sequential",
        "steps": [
            {"id": "write", "type": "tool", "command": "@writer --text=x"},
            {"id": "end", "type": "end", "depends_on": ["write"]},
        ],
    }
    result = validate_workflow_definition(definition)
    assert result.valid
    assert result.errors == []
    # Derived from the steps, so it is available without a second parse.
    assert result.required_tools == ["writer"]


def test_dangling_dependency_flagged():
    definition = {
        "name": "Dangling",
        "steps": [
            {"id": "a", "type": "tool", "command": "@writer", "depends_on": ["ghost"]},
        ],
    }
    result = validate_workflow_definition(definition)
    assert not result.valid
    assert any(e.code == "DAG_INVALID" for e in result.errors)


def test_cycle_flagged():
    definition = {
        "name": "Cycle",
        "steps": [
            {"id": "a", "type": "tool", "command": "@writer", "depends_on": ["b"]},
            {"id": "b", "type": "tool", "command": "@writer", "depends_on": ["a"]},
        ],
    }
    result = validate_workflow_definition(definition)
    assert not result.valid
    assert any(e.code == "DAG_INVALID" for e in result.errors)


def test_duplicate_step_ids_flagged():
    definition = {
        "name": "Dupe",
        "steps": [
            {"id": "a", "type": "tool", "command": "@writer"},
            {"id": "a", "type": "tool", "command": "@writer"},
        ],
    }
    result = validate_workflow_definition(definition)
    assert not result.valid
    # Pydantic catches duplicate ids at the shape layer.
    assert any(e.code in {"SHAPE_INVALID", "DAG_INVALID"} for e in result.errors)


def test_unknown_tool_reference_flagged_with_step_and_tool():
    definition = {
        "name": "BadTool",
        "steps": [
            {"id": "a", "type": "tool", "command": "@totally_unknown_tool_xyz --x=1"},
            {"id": "end", "type": "end", "depends_on": ["a"]},
        ],
    }
    result = validate_workflow_definition(definition)
    assert not result.valid
    issue = next(e for e in result.errors if e.code == "TOOL_UNKNOWN")
    assert issue.step_id == "a"
    assert issue.tool_key == "totally_unknown_tool_xyz"


def test_required_tools_empty_when_invalid():
    """Nothing to store when validation failed - the caller must not persist a
    half-derived tool set off an unusable definition."""
    definition = {
        "name": "Bad",
        "steps": [{"id": "a", "type": "tool", "command": "@nope_not_a_tool_zzz"}],
    }
    result = validate_workflow_definition(definition)
    assert not result.valid
    assert any(e.code == "TOOL_UNKNOWN" for e in result.errors)
    assert result.required_tools == []


# ----------------------------------------------------------------- advisory


def _file_binding_definition(ref: str) -> dict:
    return {
        "name": "Files",
        "steps": [
            {
                "id": "sink",
                "type": "tool",
                "command": "@writer --text=x",
                "file_bindings": {"source": ref},
            },
            {"id": "end", "type": "end", "depends_on": ["sink"]},
        ],
    }


def test_slot_referencing_declared_file_input_is_clean():
    inputs = [WorkflowInputField(key="pic", label="Picture", input="file")]
    result = validate_workflow_definition(_file_binding_definition("${inputs.pic}"), inputs=inputs)
    assert result.valid
    assert result.warnings == []


def test_slot_referencing_undeclared_input_warns_but_stays_valid():
    result = validate_workflow_definition(_file_binding_definition("${inputs.ghost}"), inputs=[])
    assert result.valid
    warning = next(w for w in result.warnings if w.code == "NON_FILE_INPUT_IN_SLOT")
    assert warning.step_id == "sink"
    assert "ghost" in warning.message


def test_slot_referencing_non_file_input_warns():
    inputs = [WorkflowInputField(key="text", label="Text", input="textarea")]
    result = validate_workflow_definition(_file_binding_definition("${inputs.text}"), inputs=inputs)
    assert result.valid
    warning = next(w for w in result.warnings if w.code == "NON_FILE_INPUT_IN_SLOT")
    assert "not a file input" in warning.message


def test_slot_referencing_step_output_is_not_warned():
    result = validate_workflow_definition(_file_binding_definition("${outputs.p.image}"), inputs=[])
    assert result.valid
    assert result.warnings == []
