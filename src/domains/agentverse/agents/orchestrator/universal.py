from __future__ import annotations

import time
import uuid
from typing import Any

from src.base.infrastructure.ai.llm_usage import collect_llm_usage
from src.domains.agentverse.agents.orchestrator.context import OrchestrationContext, PhaseHandler
from src.domains.agentverse.agents.orchestrator.phases import Phase
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.utils.run_trace import TRACE_KEY, utc_now_iso


def _apply_addons_to_ctx(
    ctx: OrchestrationContext,
    addons: dict[str, Any] | None,
    *,
    tool_id: str,
    phase: str,
) -> None:
    """
    Generic lifting of tool 'addons' into the orchestration context.

    Expected structure (convention, not enforced):
      addons = {
        "reports": { "auditor": {...}, "rubric": {...}, ... },
        "usage": { ... },
      }
    """
    if not isinstance(addons, dict):
        return

    reports = addons.get("reports")
    if isinstance(reports, dict):
        report_bag = ctx.setdefault("reports", {})
        for name, value in reports.items():
            report_bag[name] = value

    usage = addons.get("usage")
    if usage is not None:
        usage_bag = ctx.setdefault("usage", {})
        usage_bag[tool_id] = usage

    ctx.setdefault("addons_log", []).append({"tool": tool_id, "phase": phase, "addons": addons})


def _maybe_lift_artifact_addons(ctx: OrchestrationContext, *, phase: str) -> None:
    """
    If a phase handler attached ctx["artifact"] in the standard format:
      artifact = {"kind": ..., "content": ..., "meta": {"tool": "...", "addons": {...}}}
    then lift meta.addons into ctx-level bags for telemetry/reports.
    """
    art = ctx.get("artifact")
    if not isinstance(art, dict):
        return

    meta = art.get("meta")
    if not isinstance(meta, dict):
        return

    addons = meta.get("addons")
    if not isinstance(addons, dict):
        return

    tool_id = meta.get("tool") or "unknown"
    _apply_addons_to_ctx(ctx, addons, tool_id=str(tool_id), phase=phase)


def _seed_run_identity(
    ctx: OrchestrationContext, extra: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mint the turn's run identity and open its telemetry sinks.

    The tool trace is threaded into ``tool_kwargs`` because that is the dict the
    phase handlers already forward down to ``ToolManager.execute`` - the same
    route ``_socket_emitter`` takes. The LLM sink is a context variable instead,
    since ``get_response_async`` sits behind call sites that pass nothing.
    """
    ctx.setdefault("run_id", uuid.uuid4().hex)
    ctx.setdefault("started_at", utc_now_iso())

    # Literal keys: OrchestrationContext is a TypedDict. TRACE_KEY names the
    # same field for the plain dicts the sink is threaded through below.
    tool_trace: list[dict[str, Any]] = ctx.setdefault("_tool_trace", [])
    llm_calls: list[dict[str, Any]] = ctx.setdefault("_llm_calls", [])

    tool_kwargs = extra.get("tool_kwargs")
    if isinstance(tool_kwargs, dict):
        tool_kwargs[TRACE_KEY] = tool_trace

    return tool_trace, llm_calls


def _stamp_phase(trace: list[dict[str, Any]], from_index: int, phase: str) -> None:
    """Attribute the steps recorded during a phase to that phase.

    ``execute_router`` has no idea which phase called it, and should not; the
    loop is the only place that knows, so it back-fills the label here.
    """
    for step in trace[from_index:]:
        if isinstance(step, dict) and step.get("phase") is None:
            step["phase"] = phase


class UniversalOrchestrator:
    """
    Universal, role-agnostic orchestration loop.

    CONTROL PLANE only:
    - executes phases in a fixed order
    - measures phase timing
    - records phase logs

    IMPORTANT:
    - It does NOT classify tools
    - It does NOT short-circuit tool execution
    - Tool routing/execution belongs in ToolManager.execute_router
    """

    def __init__(self, *, max_refine_loops: int = 0):
        self.max_refine_loops = max_refine_loops

    async def plan_and_execute(
        self,
        *,
        handlers: dict[str, PhaseHandler],
        ctx: OrchestrationContext,
        extra: dict[str, Any] | None = None,
    ) -> OrchestrationContext:
        extra = extra or {}
        extra["history"] = ctx.get("history") or []

        tool_trace, llm_calls = _seed_run_identity(ctx, extra)

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🔄 LOOP] loop_start",
            extra={
                "phases": list(handlers.keys()),
                "max_refine_loops": self.max_refine_loops,
            },
        )

        ctx.setdefault("metrics", {})
        ctx.setdefault("phase_log", [])

        order = [
            Phase.ANALYZE.value,
            Phase.PLAN.value,
            Phase.TASK.value,
            Phase.REFINE.value,
            Phase.EVALUATE.value,
        ]

        with collect_llm_usage(llm_calls):
            for phase in order:
                t0 = time.perf_counter()
                trace_mark = len(tool_trace)

                AGENTVERSE_LOGGER.info(
                    LogDomain.UNIVERSAL_ORCHESTRATOR,
                    f"[🧭 ORCH][🚀 {phase.upper()}] phase_start",
                    extra={"phase": phase},
                )

                handler = handlers.get(phase)

                if handler is None:
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    ctx["metrics"][f"{phase}_ms"] = elapsed_ms
                    ctx["phase_log"].append({"phase": phase, "ms": elapsed_ms, "noop": True})

                    AGENTVERSE_LOGGER.debug(
                        LogDomain.UNIVERSAL_ORCHESTRATOR,
                        f"[🧭 ORCH][⏭️ {phase.upper()}] phase_noop",
                        extra={
                            "phase": phase,
                            "duration_ms": elapsed_ms,
                        },
                    )
                    continue

                try:
                    ctx = await handler(ctx, extra)

                    artifact = ctx.get("artifact") or {}
                    artifact_kind = artifact.get("kind")
                    content = artifact.get("content")
                    content_len = len(content) if isinstance(content, str) else -1

                    AGENTVERSE_LOGGER.debug(
                        LogDomain.UNIVERSAL_ORCHESTRATOR,
                        f"[🧭 ORCH][🧾 {phase.upper()}] phase_ctx_snapshot",
                        extra={
                            "phase": phase,
                            "artifact_kind": artifact_kind,
                            "content_len": content_len,
                        },
                    )

                except Exception as e:
                    AGENTVERSE_LOGGER.exception(
                        LogDomain.UNIVERSAL_ORCHESTRATOR,
                        f"[🧭 ORCH][💥 {phase.upper()}] phase_failed",
                        extra={
                            "phase": phase,
                            "error": str(e),
                        },
                    )
                    ctx.setdefault("phase_log", []).append(
                        {"phase": phase, "error": str(e), "handler_failed": True}
                    )

                _maybe_lift_artifact_addons(ctx, phase=phase)
                _stamp_phase(tool_trace, trace_mark, phase)

                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                ctx["metrics"][f"{phase}_ms"] = elapsed_ms
                ctx["phase_log"].append({"phase": phase, "ms": elapsed_ms})

                AGENTVERSE_LOGGER.info(
                    LogDomain.UNIVERSAL_ORCHESTRATOR,
                    f"[🧭 ORCH][🏁 {phase.upper()}] phase_complete",
                    extra={
                        "phase": phase,
                        "duration_ms": elapsed_ms,
                    },
                )

        ctx["ended_at"] = utc_now_iso()

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🏁 LOOP] loop_complete",
            extra={"metrics": ctx.get("metrics")},
        )

        return ctx
