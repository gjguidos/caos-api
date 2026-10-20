from __future__ import annotations

import asyncio
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.workflow_tool.dag import (
    any_step_failed,
    initialise_step_runtime,
    normalise_sequential_dependencies,
    ready_steps,
    validate_dag,
)
from src.domains.agentverse.tools.workflow_tool.helpers.output_store import (
    read_binding_outputs,
    store_display_output,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.step_with_retry import (
    execute_step_with_retry as _execute_step_with_retry,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.mark_batch_running import (
    mark_batch_running as _mark_batch_running,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.save import (
    save as _save,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.status import (
    resolve_run_status as _resolve_run_status,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.step_result import (
    apply_step_result as _apply_step_result,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    StepExecutionResult,
    StepRuntime,
    WorkflowDefinition,
    WorkflowRun,
    now_utc_iso,
)
from src.domains.agentverse.tools.workflow_tool.protocols import (
    ToolRunnerLike,
    WorkflowStepEventCallback,
)


class WorkflowEngine:
    def __init__(
        self,
        *,
        max_steps_per_call: int = 50,
        max_concurrency: int = 4,
    ) -> None:
        self.max_steps_per_call = max_steps_per_call
        self.max_concurrency = max(1, max_concurrency)

    async def run_until_pause_or_done(
        self,
        *,
        run: WorkflowRun,
        wf: WorkflowDefinition,
        tool_runner: ToolRunnerLike,
        store: Any,
        context: dict[str, Any] | None = None,
        on_step_event: WorkflowStepEventCallback | None = None,
    ) -> tuple[WorkflowRun, dict[str, Any]]:
        if getattr(wf, "execution_mode", "sequential") == "sequential":
            wf = normalise_sequential_dependencies(wf)
        validate_dag(wf)
        initialise_step_runtime(run, wf)

        outputs: dict[str, Any] = {}
        executed_count = 0
        context = context or {}

        while executed_count < self.max_steps_per_call:
            ready = ready_steps(wf, run)

            if not ready:
                _resolve_run_status(wf=wf, run=run)
                await _save(run, store)
                self._log_return_outputs(
                    run=run,
                    outputs=outputs,
                    reason="no ready steps",
                )
                return run, outputs

            batch = ready[: self.max_concurrency]

            _mark_batch_running(run=run, batch=batch)

            for step in batch:
                await _record_step_event(
                    run=run,
                    step_id=step.id,
                    status="RUNNING",
                    on_step_event=on_step_event,
                )

            results = await asyncio.gather(
                *[
                    _execute_step_with_retry(
                        run=run,
                        wf=wf,
                        step=step,
                        tool_runner=tool_runner,
                        context=context,
                    )
                    for step in batch
                ],
                return_exceptions=True,
            )

            for step, result in zip(batch, results, strict=False):
                executed_count += 1

                if isinstance(result, Exception):
                    step_result = StepExecutionResult(
                        step_id=step.id,
                        ok=False,
                        status="FAILED",
                        error=str(result),
                    )
                else:
                    step_result = result

                await _apply_step_result(
                    run=run,
                    step=step,
                    result=step_result,
                    outputs=outputs,
                    store=store,
                )

                runtime = run.steps.get(step.id)
                terminal_status = getattr(runtime, "status", None) or step_result.status
                await _record_step_event(
                    run=run,
                    step_id=step.id,
                    status=terminal_status,
                    payload=step_result.output,
                    on_step_event=on_step_event,
                )

            if run.paused or run.waiting_events:
                run.status = "PAUSED"
                await _save(run, store)
                self._log_return_outputs(
                    run=run,
                    outputs=outputs,
                    reason="paused",
                )
                return run, outputs

            if any_step_failed(wf, run):
                run.status = "FAILED"
                run.last_error = run.last_error or "One or more workflow steps failed."
                await _save(run, store)
                self._log_return_outputs(
                    run=run,
                    outputs=outputs,
                    reason="failed",
                )
                return run, outputs

            await _save(run, store)

        run.status = "RUNNING"
        await _save(run, store)

        self._log_return_outputs(
            run=run,
            outputs=outputs,
            reason="max steps reached",
        )

        return run, outputs

    async def cancel_pause(
        self,
        *,
        run: WorkflowRun,
        step_id: str,
        payload: dict[str, Any],
        store: Any,
        on_step_event: WorkflowStepEventCallback | None = None,
    ) -> WorkflowRun:
        runtime = run.steps.get(step_id)

        if runtime is not None:
            if isinstance(runtime, dict):
                runtime = StepRuntime.model_validate(runtime)
                run.steps[step_id] = runtime

            runtime.status = "CANCELLED"
            runtime.last_error = "Cancelled by user"
            runtime.completed_at = now_utc_iso()

        reason = payload.get("reason") or "user_cancelled"
        store_display_output(
            run.state,
            step_id,
            "workflow",
            {
                "ok": False,
                "status": "CANCELLED",
                "message": "Cancelled by user",
                "error": reason,
            },
        )

        run.state.setdefault("resume_payloads", {})[step_id] = payload or {}

        if run.paused:
            run.paused.pop(step_id, None)

        if run.waiting_events:
            run.waiting_events.pop(step_id, None)

        run.status = "CANCELLED"
        run.last_error = "Workflow cancelled by user"

        await _save(run, store)
        await _record_step_event(
            run=run,
            step_id=step_id,
            status="CANCELLED",
            on_step_event=on_step_event,
        )
        return run

    async def resume_step(
        self,
        *,
        run: WorkflowRun,
        step_id: str,
        payload: dict[str, Any] | None = None,
        store: Any,
        on_step_event: WorkflowStepEventCallback | None = None,
    ) -> WorkflowRun:
        runtime = run.steps.setdefault(step_id, StepRuntime())

        if isinstance(runtime, dict):
            runtime = StepRuntime.model_validate(runtime)
            run.steps[step_id] = runtime

        is_paused = (
            step_id in (run.paused or {})
            or step_id in (run.waiting_events or {})
            or runtime.status == "PAUSED"
        )

        if not is_paused:
            raise ValueError(
                f"Step '{step_id}' is not paused. "
                f"run_status={run.status}, "
                f"runtime_status={runtime.status}, "
                f"paused_keys={list((run.paused or {}).keys())}, "
                f"waiting_keys={list((run.waiting_events or {}).keys())}"
            )

        run.paused.pop(step_id, None)
        run.waiting_events.pop(step_id, None)

        run.state.setdefault("resume_payloads", {})[step_id] = payload or {}

        runtime.status = "COMPLETED"
        runtime.completed_at = now_utc_iso()

        run.status = "RUNNING"

        await _save(run, store)
        await _record_step_event(
            run=run,
            step_id=step_id,
            status="COMPLETED",
            on_step_event=on_step_event,
        )
        return run

    def _log_return_outputs(
        self,
        *,
        run: WorkflowRun,
        outputs: dict[str, Any],
        reason: str,
    ) -> None:
        state_outputs = read_binding_outputs(run.state)
        output_keys = set((outputs or {}).keys())
        state_output_keys = set(state_outputs.keys())
        paused_keys = set((run.paused or {}).keys())

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[WORKFLOW DEBUG] engine_return_outputs",
            extra={
                "reason": reason,
                "status": run.status,
                "output_keys": list(output_keys),
                "state_output_keys": list(state_output_keys),
                "paused": list(paused_keys),
            },
        )


async def _record_step_event(
    *,
    run: WorkflowRun,
    step_id: str,
    status: str,
    payload: Any = None,
    on_step_event: WorkflowStepEventCallback | None = None,
) -> None:
    if on_step_event is None:
        return

    workflow_key = (run.state.get("_workflow") or {}).get("key")
    event: dict[str, Any] = {
        "type": "workflow.step",
        "run_id": run.run_id,
        "workflow_key": workflow_key,
        "step_id": step_id,
        "status": status,
    }
    if payload is not None:
        event["payload"] = payload

    try:
        await on_step_event(event)
    except Exception:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[WORKFLOW] failed to notify workflow.step event",
            extra={"run_id": run.run_id, "step_id": step_id, "status": status},
        )
