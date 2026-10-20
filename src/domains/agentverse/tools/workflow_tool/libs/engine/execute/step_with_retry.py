from __future__ import annotations

import asyncio
from typing import Any

from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.step_readonly import (
    execute_step_readonly,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.retry import (
    retry_delay,
    retry_policy,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    RetryPolicy,
    StepExecutionResult,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from src.domains.agentverse.tools.workflow_tool.protocols import ToolRunnerLike


async def execute_step_with_retry(
    *,
    run: WorkflowRun,
    wf: WorkflowDefinition,
    step: WorkflowStep,
    tool_runner: ToolRunnerLike,
    context: dict[str, Any],
) -> StepExecutionResult:
    retry = retry_policy(step) if step.type == "tool" else RetryPolicy(attempts=1)
    attempts = max(1, retry.attempts)

    last_result: StepExecutionResult | None = None

    for attempt in range(1, attempts + 1):
        result = await execute_step_readonly(
            run=run,
            wf=wf,
            step=step,
            tool_runner=tool_runner,
            context=context,
        )

        if result.ok:
            return result

        last_result = result

        if attempt < attempts:
            await asyncio.sleep(retry_delay(retry, attempt))

    return last_result or StepExecutionResult(
        step_id=step.id,
        ok=False,
        status="FAILED",
        error=f"Step failed after {attempts} attempts",
    )
