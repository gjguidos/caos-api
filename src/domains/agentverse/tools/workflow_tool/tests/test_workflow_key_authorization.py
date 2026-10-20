"""Key-based workflow resolution is authorized against the agent's declared set.

Before this, ``workflow_key or workflow_name or cfg.workflow_key`` let a
caller-supplied key beat the agent's own config, so any agent holding the
workflow tool could run any workflow in the catalog. The agent's declared
``workflows`` is now the sole authorization set, and it is the *only* way to
reach an executable definition - there is no inline path left to bypass it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY, ManagerFacts
from src.domains.agentverse.tools.workflow_tool import WorkflowTool
from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig
from src.domains.agentverse.tools.workflow_tool.helpers.authorize_key import (
    authorize_workflow_key,
)
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition

_DEF = {
    "execution_mode": "sequential",
    "steps": [
        {"id": "write", "type": "tool", "command": "@writer --text=a"},
        {"id": "end", "type": "end", "depends_on": ["write"]},
    ],
}


def _tool_with_catalog() -> WorkflowTool:
    tool = WorkflowTool(config=WorkflowToolConfig())
    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(
        return_value=(WorkflowDefinition.model_validate(_DEF), "Mini", [])
    )
    request = MagicMock()
    request.app.container.agentverse.workflow_service.return_value = workflow_service
    tool.request = request
    return tool


class TestDeclaredSetGate:
    def test_declared_key_is_allowed(self) -> None:
        authorize_workflow_key("wf_a", declared={"wf_a", "wf_b"})

    def test_undeclared_key_is_rejected(self) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            authorize_workflow_key("wf_evil", declared={"wf_a"})

        message = str(excinfo.value)
        assert "wf_evil" in message
        assert "not attached" in message

    def test_rejection_names_what_is_available(self) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            authorize_workflow_key("wf_evil", declared={"wf_a", "wf_b"})

        assert "wf_a, wf_b" in str(excinfo.value)

    def test_tool_config_cannot_authorize_a_key(self) -> None:
        """``agent.workflows`` is the sole declaration; a key that only appears in
        the tool's own config no longer buys itself authorization."""
        assert "workflow_key" not in WorkflowToolConfig.model_fields

        with pytest.raises(ToolExecutionError):
            authorize_workflow_key("wf_pinned", declared=set())

    def test_empty_declared_set_rejects_every_key(self) -> None:
        with pytest.raises(ToolExecutionError):
            authorize_workflow_key("wf_evil", declared=set())


class TestSlugEquivalence:
    def test_case_and_spacing_differences_still_match(self) -> None:
        """The catalog slugifies on read, so an attached key and a requested one
        can differ in case or spacing and still address the same row."""
        authorize_workflow_key("My Workflow", declared={"my_workflow"})


class TestFailsClosed:
    @pytest.mark.asyncio
    async def test_missing_context_key_does_not_open_the_gate(self) -> None:
        """``build_effective_context`` sets ``ManagerFacts`` on every manager-routed
        execution, so absence means the call did not come through the manager. An
        authorization gate must not read that as permission."""
        tool = _tool_with_catalog()

        with pytest.raises(ToolExecutionError, match="not attached"):
            await tool._resolve_workflow(
                op="start",
                existing_run=None,
                workflow_key="mini",
                context={},
            )

        tool.request.app.container.agentverse.workflow_service.assert_not_called()


class TestResolutionIsGated:
    @pytest.mark.asyncio
    async def test_caller_key_cannot_reach_an_unattached_workflow(self) -> None:
        """The whole point: a caller-supplied key used to beat the pinned config,
        so any agent with the workflow tool could run the entire catalog."""
        tool = _tool_with_catalog()

        with pytest.raises(ToolExecutionError, match="not attached"):
            await tool._resolve_workflow(
                op="start",
                existing_run=None,
                workflow_key="someone_elses_workflow",
                context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
            )

        tool.request.app.container.agentverse.workflow_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_attached_key_resolves_normally(self) -> None:
        tool = _tool_with_catalog()

        _wf, name, _inputs = await tool._resolve_workflow(
            op="start",
            existing_run=None,
            workflow_key="mini",
            context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
        )

        assert name == "Mini"

    @pytest.mark.asyncio
    async def test_an_inline_definition_can_no_longer_be_supplied(self) -> None:
        """The inline path is gone, parameter included. It was the one way to
        build an arbitrary DAG - and a one-step DAG reaches workflow-scoped tools
        that are deliberately kept off the direct-invocation surface.
        """
        tool = _tool_with_catalog()

        assert "workflow" not in WorkflowTool.parameters

        with pytest.raises(TypeError):
            await tool._resolve_workflow(
                op="start",
                existing_run=None,
                workflow=_DEF,
                workflow_key=None,
                context={CONTEXT_KEY: ManagerFacts()},
            )

        tool.request.app.container.agentverse.workflow_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_key_is_rejected_and_names_the_alternatives(self) -> None:
        """No implicit fallback: without a key there is nothing to resolve, and
        the error says which keys would have worked."""
        tool = _tool_with_catalog()

        with pytest.raises(ToolExecutionError) as excinfo:
            await tool._resolve_workflow(
                op="start",
                existing_run=None,
                workflow_key=None,
                context={
                    CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini", "other"}))
                },
            )

        message = str(excinfo.value)
        assert "workflow_key is required" in message
        assert "mini, other" in message
        tool.request.app.container.agentverse.workflow_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_from_snapshot_is_not_gated(self) -> None:
        """A paused run resumes from its own snapshot, so detaching the workflow
        mid-run must not strand it."""
        tool = _tool_with_catalog()
        existing_run = MagicMock()
        existing_run.definition = _DEF
        existing_run.workflow_name = "Mini"

        _wf, name, _inputs = await tool._resolve_workflow(
            op="resume",
            existing_run=existing_run,
            workflow_key="mini",
            context={CONTEXT_KEY: ManagerFacts()},
        )

        assert name == "Mini"
