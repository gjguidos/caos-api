from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.helpers.state_patch import apply_state_patch
from src.domains.agentverse.tools.workflow_tool.models import (
    StepExecutionResult,
    StepRuntime,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)


async def apply_step_result(
    *,
    run: WorkflowRun,
    step: WorkflowStep,
    result: StepExecutionResult,
    outputs: dict[str, Any],
    store: Any,
) -> None:
    runtime = run.steps.setdefault(step.id, StepRuntime())

    if not result.ok:
        runtime.status = "FAILED"
        runtime.last_error = result.error or f"Step failed: {step.id}"
        runtime.completed_at = now_utc_iso()
        run.last_error = runtime.last_error

        if result.output is not None:
            outputs[step.id] = result.output

        return

    if result.output is not None:
        outputs[step.id] = result.output

    if result.state_patch:
        apply_state_patch(run, result.state_patch)

    if result.pause is not None:
        run.paused[step.id] = result.pause

    if result.wait_for is not None:
        run.waiting_events[step.id] = result.wait_for

    if result.event_index is not None:
        await store.put_event_wait_index(
            event_type=result.event_index["event_type"],
            correlation_id=result.event_index["correlation_id"],
            run_id=result.event_index["run_id"],
            step_id=result.event_index["step_id"],
        )

    runtime.status = result.status
    runtime.completed_at = now_utc_iso() if result.status in {"COMPLETED", "FAILED"} else None
