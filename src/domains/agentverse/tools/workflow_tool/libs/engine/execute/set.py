from __future__ import annotations

from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    render_template,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.render_ctx import (
    render_ctx as _render_ctx,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    StepExecutionResult,
    WorkflowRun,
    WorkflowStep,
)


async def execute_set(
    run: WorkflowRun,
    step: WorkflowStep,
) -> StepExecutionResult:
    if not step.set:
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="FAILED",
            error=f"set step '{step.id}' missing payload",
        )

    try:
        rendered = render_template(
            step.set,
            _render_ctx(run),
            strict=True,
        )
    except MissingTemplateValueError as exc:
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="FAILED",
            error=str(exc),
        )

    if not isinstance(rendered, dict):
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="EXECUTE SET FAILED",
            error=f"set step '{step.id}' rendered non-object payload",
        )

    return StepExecutionResult(
        step_id=step.id,
        ok=True,
        status="COMPLETED",
        output={"set": rendered},
        state_patch=dict(rendered),
    )
