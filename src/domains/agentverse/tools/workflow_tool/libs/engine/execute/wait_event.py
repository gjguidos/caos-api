from __future__ import annotations

from src.domains.agentverse.tools.workflow_tool.helpers.response import dump_model
from src.domains.agentverse.tools.workflow_tool.models import (
    EventWaitSpec,
    StepExecutionResult,
    WorkflowRun,
    WorkflowStep,
)


async def execute_wait_event(
    run: WorkflowRun,
    step: WorkflowStep,
) -> StepExecutionResult:
    if not step.wait_for:
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="EXECUTE WAIT EVENT FAILED",
            error=f"wait_event step '{step.id}' missing wait_for",
        )

    wait_for = (
        step.wait_for
        if isinstance(step.wait_for, EventWaitSpec)
        else EventWaitSpec.model_validate(step.wait_for)
    )

    return StepExecutionResult(
        step_id=step.id,
        ok=True,
        status="PAUSED",
        wait_for=wait_for,
        event_index={
            "event_type": wait_for.event_type,
            "correlation_id": wait_for.correlation_id,
            "run_id": run.run_id,
            "step_id": step.id,
        },
        output={"waiting_for": dump_model(wait_for)},
    )
