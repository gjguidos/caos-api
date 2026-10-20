"""Tests for WS-D (Mongo resolution swap) and WS-E (snapshot-on-run)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY, ManagerFacts
from src.domains.agentverse.tools.workflow_tool import WorkflowTool
from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition, WorkflowRun
from src.domains.agentverse.tools.workflow_tool.opts import now_utc_iso

_DEF_A = {
    "execution_mode": "sequential",
    "steps": [
        {"id": "write", "type": "tool", "command": "@writer --text=a"},
        {"id": "end", "type": "end", "depends_on": ["write"]},
    ],
}

_DEF_B = {
    "execution_mode": "sequential",
    "steps": [
        {"id": "write", "type": "tool", "command": "@writer --text=b"},
        {"id": "extra", "type": "tool", "command": "@writer --text=more", "depends_on": ["write"]},
        {"id": "end", "type": "end", "depends_on": ["extra"]},
    ],
}


def _tool() -> WorkflowTool:
    return WorkflowTool(config=WorkflowToolConfig())


def _run_with_snapshot(definition: dict, *, workflow_name: str = "Version A") -> WorkflowRun:
    now = now_utc_iso()
    return WorkflowRun(
        run_id="run1",
        workflow_name=workflow_name,
        created_at=now,
        updated_at=now,
        status="PAUSED",
        definition=definition,
    )


# ----------------------------------------------------------------- WS-D


@pytest.mark.asyncio
async def test_start_resolves_from_mongo_by_key():
    tool = _tool()

    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(
        return_value=(WorkflowDefinition.model_validate(_DEF_A), "Version A", [])
    )
    request = MagicMock()
    request.app.container.agentverse.workflow_service.return_value = workflow_service
    tool.request = request

    wf, name, _inputs = await tool._resolve_workflow(
        op="start",
        existing_run=None,
        workflow_key="mini",
        context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
    )

    assert name == "Version A"
    assert [s.id for s in wf.steps] == ["write", "end"]
    workflow_service.resolve_definition.assert_awaited_once_with("mini")


@pytest.mark.asyncio
async def test_unreachable_container_is_not_reported_as_a_missing_row():
    """A non-request value under ``self.request`` (a plain string has no ``.app``)
    is an infrastructure failure, and says so. Blaming the catalog would send an
    admin hunting for a workflow row that is sitting right there.

    The orchestrator binds the real Starlette request via ``getattr(agent,
    "request")``, so this is defence-in-depth, not the live path.
    """
    tool = _tool()
    tool.request = "draw me a picture of mini_workflow_v1"

    from src.domains.agentverse.tools.base import ToolExecutionError

    # The key is authorized, so the failure is resolution, not the authz gate.
    with pytest.raises(ToolExecutionError, match="no request-bound container"):
        await tool._resolve_workflow(
            op="start",
            existing_run=None,
            workflow_key="mini",
            context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
        )


@pytest.mark.asyncio
async def test_absent_catalog_row_is_reported_as_such():
    """The other side of the split: the container works fine, the row genuinely
    is not there."""
    from src.domains.agentverse.exceptions import WorkflowNotFoundError
    from src.domains.agentverse.tools.base import ToolExecutionError

    tool = _tool()
    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(side_effect=WorkflowNotFoundError("mini"))
    tool.request = MagicMock()
    tool.request.app.container.agentverse.workflow_service.return_value = workflow_service

    with pytest.raises(ToolExecutionError, match="is not in the catalog"):
        await tool._resolve_workflow(
            op="start",
            existing_run=None,
            workflow_key="mini",
            context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
        )


@pytest.mark.asyncio
async def test_missing_key_raises_before_any_container_access():
    """With no key at all there is nothing to resolve, so resolution fails on the
    key long before it reaches the catalog."""
    tool = _tool()
    from src.domains.agentverse.tools.base import ToolExecutionError

    with pytest.raises(ToolExecutionError, match="workflow_key is required"):
        await tool._resolve_workflow(
            op="start",
            existing_run=None,
            workflow_key=None,
        )


# ----------------------------------------------------------------- WS-E


@pytest.mark.asyncio
async def test_resume_uses_snapshot_even_after_source_edited():
    tool = _tool()

    # The live row has been edited to Version B...
    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(
        return_value=(WorkflowDefinition.model_validate(_DEF_B), "Version B (edited)", [])
    )
    request = MagicMock()
    request.app.container.agentverse.workflow_service.return_value = workflow_service
    tool.request = request

    # ...but the paused run snapshotted Version A.
    existing_run = _run_with_snapshot(_DEF_A)

    wf, name, _inputs = await tool._resolve_workflow(
        op="resume",
        existing_run=existing_run,
        workflow_key="mini",
    )

    assert name == "Version A"
    assert [s.id for s in wf.steps] == ["write", "end"]
    # Snapshot wins → Mongo is never consulted on resume.
    workflow_service.resolve_definition.assert_not_called()


@pytest.mark.asyncio
async def test_resume_without_snapshot_falls_back_to_mongo():
    """Legacy runs created before WS-E have no snapshot → re-resolve by key."""
    tool = _tool()

    workflow_service = MagicMock()
    workflow_service.resolve_definition = AsyncMock(
        return_value=(WorkflowDefinition.model_validate(_DEF_B), "Version B (edited)", [])
    )
    request = MagicMock()
    request.app.container.agentverse.workflow_service.return_value = workflow_service
    tool.request = request

    legacy_run = WorkflowRun(
        run_id="legacy",
        workflow_name="x",
        created_at=now_utc_iso(),
        updated_at=now_utc_iso(),
        status="PAUSED",
        definition=None,
    )

    wf, name, _inputs = await tool._resolve_workflow(
        op="resume",
        existing_run=legacy_run,
        workflow_key="mini",
        context={CONTEXT_KEY: ManagerFacts(declared_workflow_keys=frozenset({"mini"}))},
    )

    assert name == "Version B (edited)"
    assert [s.id for s in wf.steps] == ["write", "extra", "end"]
    workflow_service.resolve_definition.assert_awaited_once_with("mini")


def test_new_run_persists_definition_snapshot():
    tool = _tool()
    wf = WorkflowDefinition.model_validate(_DEF_A)

    run = tool._new_run(
        wf=wf, run_id="r1", inputs={}, workflow_key="mini", workflow_name="Version A"
    )

    assert run.definition is not None
    assert "name" not in run.definition
    assert [s["id"] for s in run.definition["steps"]] == ["write", "end"]
    assert run.workflow_name == "Version A"
