"""``op=start`` requires an explicit ``workflow_key``, canonicalized once.

There is no implicit fallback: a run's workflow is a function of what the caller
asked for, not of agent config, so attaching a second workflow can never change
which one an existing button starts.

The key is slugified at the tool's entry point, and that single value is what is
authorized, looked up, stored on the run, and echoed back - so frontend event
correlation is reliable rather than accidentally reliable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY, ManagerFacts
from src.domains.agentverse.tools.workflow_tool import WorkflowTool
from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition
from src.domains.agentverse.tools.workflow_tool.store import InMemoryRunStore

# A single terminal step: enough to exercise resolution and run bookkeeping
# without needing a tool to execute.
_DEF = {"execution_mode": "sequential", "steps": [{"id": "end", "type": "end"}]}


def _tool() -> WorkflowTool:
    tool = WorkflowTool(config=WorkflowToolConfig())
    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(
        return_value=(WorkflowDefinition.model_validate(_DEF), "Tap Replacer", [])
    )
    request = MagicMock()
    request.app.container.agentverse.workflow_service.return_value = workflow_service
    tool.request = request
    return tool


def _context(*declared: str) -> dict:
    return {
        CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset(declared)),
        "tool_runner": AsyncMock(),
        "workflow_run_store": InMemoryRunStore(),
    }


class TestKeyIsRequired:
    @pytest.mark.asyncio
    async def test_start_without_a_key_is_rejected(self) -> None:
        tool = _tool()

        with pytest.raises(ToolExecutionError) as excinfo:
            await tool._run(None, op="start", context=_context("tap_replacer_v1", "other_flow"))

        message = str(excinfo.value)
        assert "workflow_key is required" in message
        # Self-describing: the caller is told what it could have asked for.
        assert "other_flow, tap_replacer_v1" in message

    @pytest.mark.asyncio
    async def test_explicit_key_resolves_authorizes_and_runs(self) -> None:
        tool = _tool()

        result = await tool._run(
            None,
            op="start",
            workflow_key="tap_replacer_v1",
            run_id="run_1",
            context=_context("tap_replacer_v1"),
        )

        assert result["workflow_key"] == "tap_replacer_v1"
        assert result["workflow_name"] == "Tap Replacer"
        assert result["status"] == "COMPLETED"


class TestKeyIsCanonicalized:
    @pytest.mark.asyncio
    async def test_a_non_slug_key_is_slugified_everywhere_it_lands(self) -> None:
        tool = _tool()
        context = _context("tap_replacer_v1")

        result = await tool._run(
            None,
            op="start",
            workflow_key="Tap Replacer V1",
            run_id="run_2",
            context=context,
        )

        workflow_service = tool.request.app.container.agentverse.workflow_service()
        workflow_service.resolve_definition.assert_awaited_once_with("tap_replacer_v1")

        # Reported identity, run state, and the context the step events read.
        assert result["workflow_key"] == "tap_replacer_v1"
        assert context["workflow_key"] == "tap_replacer_v1"

        run = await context["workflow_run_store"].get("run_2")
        assert run.state["_workflow"]["key"] == "tap_replacer_v1"
