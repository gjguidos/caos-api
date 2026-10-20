from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.dag import (
    all_steps_terminal,
    any_step_failed,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
)


def resolve_run_status(
    *,
    wf: WorkflowDefinition,
    run: WorkflowRun,
) -> None:
    if run.paused or run.waiting_events:
        run.status = "PAUSED"
        return

    if any_step_failed(wf, run):
        run.status = "FAILED"
        run.last_error = run.last_error or "One or more workflow steps failed."
        return

    if all_steps_terminal(wf, run):
        run.status = "COMPLETED"
        return

    run.status = "FAILED"
    run.last_error = "No ready steps available. Workflow is blocked."


def workflow_status(env: Any) -> str | None:
    if not isinstance(env, dict):
        return None

    status = env.get("status")

    return str(status).upper() if status else None
