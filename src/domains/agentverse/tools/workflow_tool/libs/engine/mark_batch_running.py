from src.domains.agentverse.tools.workflow_tool.models import (
    StepRuntime,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)


def mark_batch_running(
    *,
    run: WorkflowRun,
    batch: list[WorkflowStep],
) -> None:
    now = now_utc_iso()

    for step in batch:
        runtime = run.steps.setdefault(step.id, StepRuntime())
        runtime.status = "RUNNING"
        runtime.started_at = runtime.started_at or now
        runtime.completed_at = None
        runtime.attempts += 1
