from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.workflow_tool.helpers.output_store import (
    read_display_outputs,
    store_display_output,
)
from src.domains.agentverse.tools.workflow_tool.helpers.response import (
    build_run_snapshot,
    dump_model,
    empty_run_snapshot,
)
from src.domains.agentverse.tools.workflow_tool.helpers.response import (
    finalize_run_response as _finalize_run_response,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
)
from src.domains.agentverse.tools.workflow_tool.protocols import WorkflowEngineLike


async def handle_get(
    *,
    store: Any,
    run_id: str | None,
) -> dict[str, Any]:
    if not run_id:
        raise ToolExecutionError("run_id is required for op=get")

    run = await store.get(run_id)
    if not run:
        return empty_run_snapshot(
            run_id=run_id,
            status="NOT_FOUND",
            error=f"Run not found: {run_id}",
        )

    raw_outputs = read_display_outputs(run.state)

    return build_run_snapshot(run=run, outputs=raw_outputs)


async def handle_start(
    *,
    engine_call: Callable[..., Any],
    new_run: Callable[..., Any],
    store: Any,
    engine: WorkflowEngineLike,
    wf: WorkflowDefinition,
    workflow_key: str | None = None,
    workflow_name: str | None = None,
    run_id: str | None,
    inputs: dict[str, Any] | None,
    context: dict[str, Any],
    tool_runner: Callable[..., Any],
    emit_step_event: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    rid = run_id or f"wf_{__import__('time').time_ns()}"
    context["run_id"] = rid

    run = new_run(
        wf=wf,
        run_id=rid,
        inputs=inputs,
        workflow_key=workflow_key,
        workflow_name=workflow_name,
    )

    await store.put(run)

    run2, outputs = await engine_call(
        engine=engine,
        store=store,
        wf=wf,
        run=run,
        tool_runner=tool_runner,
        context=context,
        on_step_event=emit_step_event,
    )

    return _finalize_run_response(run=run2, outputs=outputs)


async def handle_resume(
    *,
    engine_call: Callable[..., Any],
    store: Any,
    engine: WorkflowEngineLike,
    wf: WorkflowDefinition,
    run_id: str | None,
    pause_payload: dict[str, Any] | None,
    context: dict[str, Any],
    tool_runner: Callable[..., Any],
    emit_step_event: Callable[..., Any] | None = None,
    emit_run_snapshot: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not run_id:
        raise ToolExecutionError("run_id is required for op=resume")
    if pause_payload is None:
        raise ToolExecutionError("pause_payload is required for op=resume")

    run = await _load_run_or_raise(store=store, run_id=run_id)
    _ensure_paused(run)

    context["run_id"] = run_id

    step_id = (
        pause_payload.get("step_id")
        or pause_payload.get("pause_step_id")
        or next(iter((run.paused or run.waiting_events).keys()))
    )

    action = (
        pause_payload.get("action")
        or pause_payload.get("data", {}).get("action")
        or pause_payload.get("decision")
        or "continue"
    )

    if action not in {"continue", "cancel"}:
        raise ToolExecutionError(f"Unsupported workflow pause action: {action}")

    AGENTVERSE_LOGGER.warning(
        LogDomain.TOOL_BAY,
        "[👷 WORKFLOW][🔁 RESUME DEBUG] loaded_run_before_any_resume",
        extra={
            "run_id": run_id,
            "run_status": run.status,
            "step_id": step_id,
            "action": action,
            "paused_keys": list((run.paused or {}).keys()),
            "waiting_keys": list((run.waiting_events or {}).keys()),
            "runtime_status": getattr(run.steps.get(step_id), "status", None)
            if run.steps.get(step_id) is not None
            else None,
        },
    )

    if action == "cancel":
        run = await engine.cancel_pause(
            run=run,
            step_id=step_id,
            payload=pause_payload,
            store=store,
            on_step_event=emit_step_event,
        )

        return _finalize_run_response(run=run, outputs={})

    pause = (run.paused or {}).get(step_id)
    pause_data = dump_model(pause) if pause else {}
    nested = pause_data.get("nested_workflow") or {}

    if nested:
        run = await _resume_nested_pause(
            engine=engine,
            store=store,
            run=run,
            wf=wf,
            step_id=step_id,
            pause_payload=pause_payload,
            nested=nested,
            context=context,
            tool_runner=tool_runner,
        )
    else:
        run = await engine.resume_step(
            run=run,
            step_id=step_id,
            payload=pause_payload,
            store=store,
            on_step_event=emit_step_event,
        )

        if emit_run_snapshot is not None:
            await emit_run_snapshot(build_run_snapshot(run=run, outputs={}))

    run2, outputs = await engine_call(
        engine=engine,
        store=store,
        wf=wf,
        run=run,
        tool_runner=tool_runner,
        context=context,
        on_step_event=emit_step_event,
    )

    return _finalize_run_response(run=run2, outputs=outputs)


async def _resume_nested_pause(
    *,
    engine: WorkflowEngineLike,
    store: Any,
    run: WorkflowRun,
    wf: WorkflowDefinition,
    step_id: str,
    pause_payload: dict[str, Any],
    nested: dict[str, Any],
    context: dict[str, Any],
    tool_runner: Callable[..., Any],
) -> WorkflowRun:
    child_run_id = nested.get("child_run_id")
    if not child_run_id:
        raise ToolExecutionError("Nested workflow pause missing child_run_id")

    child_payload = dict(pause_payload or {})
    child_payload.setdefault("step_id", nested.get("child_step_id"))
    child_payload.setdefault("pause_id", nested.get("child_pause_id"))

    ok, env = await tool_runner(
        "workflow",
        {
            "op": "resume",
            "run_id": child_run_id,
            "pause_payload": child_payload,
            "workflow_key": nested.get("child_workflow_key"),
            "context": {
                **context,
                "workflow_invocation": True,
                "allow_nested_workflows": True,
            },
        },
    )

    if not ok:
        raise ToolExecutionError("Nested workflow resume failed")

    if isinstance(env, dict) and str(env.get("status", "")).upper() == "PAUSED":
        parent_pause = run.paused.get(step_id)

        if parent_pause:
            parent_pause.nested_workflow = parent_pause.nested_workflow or {}
            parent_pause.nested_workflow["child_run_id"] = env.get("run_id") or child_run_id

        await store.put(run)
        return run

    compact_env = _compact_nested_result(env)

    store_display_output(run.state, step_id, "workflow", env)

    await store.put(run)

    return await engine.resume_step(
        run=run,
        step_id=step_id,
        payload={
            **(pause_payload or {}),
            "_nested_result": compact_env,
        },
        store=store,
    )


def _compact_nested_result(env: Any) -> Any:
    """Summarize a child run snapshot for the parent's resume payload."""
    if not isinstance(env, dict):
        return env

    return {
        "status": env.get("status"),
        "run_id": env.get("run_id"),
        "output_keys": list((env.get("outputs") or {}).keys()),
        # important: do NOT carry full child outputs/renderables upward
        "nested": True,
    }


async def _load_run_or_raise(*, store: Any, run_id: str) -> WorkflowRun:
    run = await store.get(run_id)
    if not run:
        raise ToolExecutionError(f"Run not found: {run_id}")
    return run


def _ensure_paused(run: WorkflowRun) -> None:
    if run.status != "PAUSED":
        raise ToolExecutionError("Run is not paused")

    if not getattr(run, "paused", None) and not getattr(run, "waiting_events", None):
        raise ToolExecutionError("Run has no paused or waiting DAG steps")
