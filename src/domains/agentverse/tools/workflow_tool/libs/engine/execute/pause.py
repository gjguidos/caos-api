from __future__ import annotations

from src.domains.agentverse.tools.workflow_tool.helpers.response import dump_model
from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    render_template,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.render_ctx import (
    render_ctx as _render_ctx,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    PauseSpec,
    PauseState,
    StepExecutionResult,
    WorkflowRun,
    WorkflowStep,
)


async def execute_pause(
    run: WorkflowRun,
    step: WorkflowStep,
) -> StepExecutionResult:
    if not step.pause:
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="FAILED",
            error=f"pause step '{step.id}' missing spec",
        )

    workflow_meta = (run.state or {}).get("_workflow") or {}

    workflow_key = (
        getattr(run, "workflow_key", None)
        or workflow_meta.get("key")
        or (run.state or {}).get("workflow_key")
    )

    workflow_name = (
        getattr(run, "workflow_name", None)
        or workflow_meta.get("name")
        or (run.state or {}).get("workflow_name")
    )

    pause_spec = (
        step.pause if isinstance(step.pause, PauseSpec) else PauseSpec.model_validate(step.pause)
    )

    try:
        prompt = render_template(
            pause_spec.prompt,
            _render_ctx(run),
            strict=True,
        )
    except MissingTemplateValueError as exc:
        return StepExecutionResult(
            step_id=step.id,
            ok=False,
            status="EXECUTE PAUSE FAILED",
            error=str(exc),
        )

    pause = PauseState(
        pause_id=f"{run.run_id}:{step.id}",
        step_id=step.id,
        prompt=str(prompt),
        workflow_key=workflow_key,
        workflow_name=workflow_name,
        actions=["continue", "cancel"],
    )

    return StepExecutionResult(
        step_id=step.id,
        ok=True,
        status="PAUSED",
        pause=pause,
        output={"paused": dump_model(pause)},
    )
