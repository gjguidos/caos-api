"""Run-start tool pre-check.

A catalog row's ``required_tools`` is derived from its DAG at save time, so the
agent factory equips exactly what the steps reach for. The remaining gap is a
*long-lived* agent: its factory set was resolved at build time, and a workflow
edited since can reference a tool it was never equipped with. The union is
re-checked at run start and fails fast by name instead of dying at step N.

Advisory only - ``evaluate_policy`` remains the gate and runs per step.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY, ManagerFacts
from src.domains.agentverse.tools.workflow_tool import WorkflowTool
from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig
from src.domains.agentverse.tools.workflow_tool.libs.engine.resolve_tool_key import (
    tool_keys_in_definition,
)
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition


def _definition(*commands: str) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "steps": [
                {"id": f"s{i}", "type": "tool", "command": cmd} for i, cmd in enumerate(commands)
            ]
        }
    )


def _tool() -> WorkflowTool:
    return WorkflowTool(config=WorkflowToolConfig())


class TestToolKeysInDefinition:
    """The one place the DAG is read for its tool set - shared with save-time
    validation so the two can never disagree."""

    def test_reads_leading_anchors_off_the_steps(self) -> None:
        assert tool_keys_in_definition(_definition("@writer draft it", "@analyze --depth=2")) == {
            "writer",
            "analyze",
        }

    def test_deduplicates_repeated_tools(self) -> None:
        assert tool_keys_in_definition(_definition("@writer one", "@writer two")) == {"writer"}

    def test_ignores_commands_without_an_anchor(self) -> None:
        assert tool_keys_in_definition(_definition("just some prose", "@writer draft it")) == {
            "writer"
        }

    def test_ignores_non_tool_steps(self) -> None:
        definition = WorkflowDefinition.model_validate(
            {
                "steps": [
                    {"id": "w", "type": "tool", "command": "@writer draft"},
                    {"id": "end", "type": "end", "depends_on": ["w"]},
                ]
            }
        )

        assert tool_keys_in_definition(definition) == {"writer"}


def _ctx(*runnable: str) -> dict:
    return {CONTEXT_KEY: ManagerFacts(runnable_tool_keys=frozenset(runnable))}


class TestToolPreCheck:
    def test_names_every_tool_the_agent_cannot_run(self) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            _tool()._assert_tools_constructible(
                definition=_definition("@writer draft", "@analyze it"),
                context=_ctx("writer"),
                workflow_key="wf_a",
            )

        message = str(excinfo.value)
        assert "analyze" in message
        assert "wf_a" in message

    def test_passes_when_every_tool_is_runnable(self) -> None:
        _tool()._assert_tools_constructible(
            definition=_definition("@writer draft", "@analyze it"),
            context=_ctx("writer", "analyze", "chat"),
            workflow_key="wf_a",
        )

    def test_missing_facts_fail_closed(self) -> None:
        """Manager-routed execution always sets the facts. Absence means the run
        did not come through the manager, which must not read as "no
        restrictions"."""
        with pytest.raises(ToolExecutionError, match="writer"):
            _tool()._assert_tools_constructible(
                definition=_definition("@writer draft"),
                context={},
                workflow_key=None,
            )

    def test_empty_inventory_reports_missing(self) -> None:
        with pytest.raises(ToolExecutionError, match="writer"):
            _tool()._assert_tools_constructible(
                definition=_definition("@writer draft"),
                context=_ctx(),
                workflow_key="wf_a",
            )
