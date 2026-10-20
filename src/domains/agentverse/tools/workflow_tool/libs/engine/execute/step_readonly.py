from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.pause import (
    execute_pause as _execute_pause,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.set import (
    execute_set as _execute_set,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import (
    execute_tool as _execute_tool,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.wait_event import (
    execute_wait_event as _execute_wait_event,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    StepExecutionResult,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from src.domains.agentverse.tools.workflow_tool.protocols import ToolRunnerLike


async def execute_step_readonly(
    *,
    run: WorkflowRun,
    wf: WorkflowDefinition,
    step: WorkflowStep,
    tool_runner: ToolRunnerLike,
    context: dict[str, Any],
) -> StepExecutionResult:
    if step.type == "end":
        return StepExecutionResult(
            step_id=step.id,
            ok=True,
            status="COMPLETED",
            output={"end": step.id},
        )

    if step.type == "set":
        return await _execute_set(run, step)

    if step.type == "pause":
        return await _execute_pause(run, step)

    if step.type == "wait_event":
        return await _execute_wait_event(run, step)

    if step.type == "tool":
        return await _execute_tool(
            run=run,
            wf=wf,
            step=step,
            tool_runner=tool_runner,
            context=context,
        )

    return StepExecutionResult(
        step_id=step.id,
        ok=False,
        status="FAILED",
        error=f"Unknown step type: {step.type}",
    )
