from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)


def workflow_context(
    *,
    run: WorkflowRun,
    wf: WorkflowDefinition,
    step: WorkflowStep,
    outer_context: dict[str, Any],
    command: str,
) -> dict[str, Any]:
    safe_context = {
        key: outer_context[key]
        for key in ("user_id", "rule_context", "file")
        if key in outer_context
    }

    outer_depth = int(outer_context.get("workflow_depth", 0) or 0)

    return {
        **safe_context,
        "workflow_invocation": True,
        "workflow_depth": outer_depth + 1,
        "allow_nested_workflows": True,
        "parent_workflow_run_id": outer_context.get("run_id"),
        "run_id": run.run_id,
        "workflow_name": run.workflow_name,
        "workflow_step_id": step.id,
        "step_id": step.id,
        "step_type": step.type,
        "workflow_inputs": dict(run.inputs or {}),
        "workflow_state": dict(run.state or {}),
        "text": command,
        "_raw_text": command,
    }
