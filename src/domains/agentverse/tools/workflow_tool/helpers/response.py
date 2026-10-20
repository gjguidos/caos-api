from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.workflow_tool.helpers.output_store import read_display_outputs
from src.domains.agentverse.tools.workflow_tool.models import WorkflowRun
from src.domains.agentverse.tools.workflow_tool.public_contract import output_display


def finalize_run_response(
    *,
    run: WorkflowRun,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    state_outputs = read_display_outputs(run.state)
    local_outputs = outputs or {}
    outputs_to_render = {**state_outputs, **local_outputs}

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[WORKFLOW] finalize_outputs",
        extra={
            "run_status": run.status,
            "local_output_keys": list(local_outputs.keys()),
            "state_output_keys": list(state_outputs.keys()),
            "render_output_keys": list(outputs_to_render.keys()),
            "paused": list((run.paused or {}).keys()),
            "waiting": list((run.waiting_events or {}).keys()),
        },
    )

    snapshot = build_run_snapshot(run=run, outputs=outputs_to_render)
    # Only the tool's own return carries this; the socket snapshot stays lean.
    snapshot["meta"] = {"addons": {"steps": _trace_steps(run)}}
    return snapshot


def dump_model(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def first_pause(run: Any) -> dict[str, Any] | None:
    paused = getattr(run, "paused", None)

    if not paused:
        return None

    if isinstance(paused, dict):
        first = next(iter(paused.values()), None)
        return _public_pause(first) if first else None

    return _public_pause(paused)


def _public_pause(pause: Any) -> dict[str, Any]:
    """Serialize a pause for the frontend, dropping internal-only plumbing."""
    data = dump_model(pause)
    data.pop("nested_workflow", None)
    return data


def empty_run_snapshot(
    *,
    run_id: str | None,
    status: str,
    error: str | None = None,
    workflow_key: str | None = None,
    workflow_name: str | None = None,
) -> dict[str, Any]:
    """Canonical ``workflow.run`` snapshot for runs with no step/output state
    (e.g. NOT_FOUND). Keeps the contract shape owned in one place."""
    return {
        "kind": "workflow.run",
        "status": status,
        "run_id": run_id,
        "workflow_key": workflow_key,
        "workflow_name": workflow_name,
        "paused": None,
        "error": error,
        "steps": {},
        "outputs": {},
    }


def _serialize_steps(run: WorkflowRun) -> dict[str, Any]:
    return {step_id: step.model_dump() for step_id, step in (run.steps or {}).items()}


def _trace_steps(run: WorkflowRun) -> list[dict[str, Any]]:
    """Project the run's steps onto the generic trace child-step shape.

    The run report records one step per tool execution, and a workflow is one
    such execution with a DAG inside it. Publishing the per-step runtime under
    ``meta.addons.steps`` lets the trace nest them as ``children`` without the
    report builder knowing anything about workflows - and a nested workflow
    nests the same way, one level deeper.
    """
    return [
        {
            "tool": step_id,
            "status": step.status,
            "attempts": step.attempts,
            "started_at": step.started_at,
            "ended_at": step.completed_at,
            "error": step.last_error,
            "children": [],
        }
        for step_id, step in (run.steps or {}).items()
    ]


def build_run_snapshot(
    *,
    run: WorkflowRun,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[WORKFLOW] build_run_snapshot",
        extra={
            "run_id": run.run_id,
            "status": run.status,
            "output_keys": list((outputs or {}).keys()),
        },
    )

    workflow_meta = run.state.get("_workflow") or {}

    return {
        "kind": "workflow.run",
        "status": run.status,
        "run_id": run.run_id,
        "workflow_key": workflow_meta.get("key"),
        "workflow_name": run.workflow_name or workflow_meta.get("name"),
        "paused": first_pause(run) if run.status == "PAUSED" else None,
        "error": run.last_error,
        "steps": _serialize_steps(run),
        "outputs": {step_id: output_display(step_id, v) for step_id, v in (outputs or {}).items()},
    }
